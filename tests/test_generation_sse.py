import asyncio
import json
from pathlib import Path
from typing import Any

from conftest import AppContext, StaticCodeGenTypeRouter
from sqlalchemy import func, select

from app.codegen_router import CodeGenType
from app.models import ChatHistory, ModelCallLog
from app.runtime.agent import AgentTurnExecutor
from app.runtime.manager import GenerationManager
from app.runtime.model import (
    ModelReply,
    ScriptedMultiFileModel,
    TextEmitter,
    ToolCall,
    UnavailableAgentModel,
)


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    for block in body.strip().split('\n\n'):
        if not block.strip():
            continue
        event: dict = {'data': None}
        for line in block.splitlines():
            if line.startswith('id: '):
                event['id'] = int(line[4:])
            elif line.startswith('event: '):
                event['event'] = line[7:]
            elif line.startswith('data: '):
                event['data'] = json.loads(line[6:])
        events.append(event)
    return events


async def _register_login_and_create_app(app_context: AppContext) -> tuple[int, int]:
    register = await app_context.client.post(
        '/api/user/register',
        json={
            'userAccount': 'generator-user',
            'userPassword': 'password123',
            'checkPassword': 'password123',
        },
    )
    user_id = register.json()['data']
    login = await app_context.client.post(
        '/api/user/login',
        json={'userAccount': 'generator-user', 'userPassword': 'password123'},
    )
    assert login.json()['code'] == 0
    created = await app_context.client.post(
        '/api/app/create',
        json={'initPrompt': '创建一个 HTML 页面'},
    )
    return user_id, created.json()['data']


async def test_html_generation_runs_tool_loop_streams_events_and_persists_session(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    request_id = 'turn-html-1'

    response = await app_context.client.post(
        '/api/app/chat/gen/code',
        json={
            'appId': app_id,
            'message': '生成一个蓝色主题的个人主页',
            'requestId': request_id,
        },
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    event_types = [event['event'] for event in events]
    assert event_types[0] == 'turn_started'
    assert 'text_delta' in event_types
    assert 'tool_started' in event_types
    assert 'tool_finished' in event_types
    tool_finished = next(event for event in events if event['event'] == 'tool_finished')
    assert tool_finished['data']['sizeBytes'] > 0
    assert event_types[-1] == 'turn_finished'
    assert all(event['data']['turnId'] == request_id for event in events)
    assert all(event['data']['sequence'] == event['id'] for event in events)
    assert [event['id'] for event in events] == sorted(event['id'] for event in events)
    assert events[-1]['data']['status'] == 'completed'

    workspace = (
        Path(app_context.app.state.settings.workspace_root) / f'html_{app_id}'
    )
    html = (workspace / 'index.html').read_text(encoding='utf-8')
    assert '蓝色主题的个人主页' in html
    assert '<!doctype html>' in html.lower()

    async with app_context.session_factory() as session:
        rows = (
            await session.scalars(
                select(ChatHistory)
                .where(ChatHistory.app_id == app_id)
                .order_by(ChatHistory.sequence)
            )
        ).all()
    assert {row.message_kind for row in rows} >= {'user', 'assistant', 'tool_call', 'tool_result'}
    assert all(row.turn_id == request_id for row in rows)
    sequences = [row.sequence for row in rows]
    assert all(sequence is not None for sequence in sequences)
    assert sequences == sorted(sequence for sequence in sequences if sequence is not None)
    assert all(row.user_id == user_id for row in rows)

    async with app_context.session_factory() as session:
        model_logs = (
            await session.scalars(
                select(ModelCallLog).where(ModelCallLog.app_id == app_id)
            )
        ).all()
    assert len(model_logs) == 2
    assert all(log.status == 'success' for log in model_logs)
    assert all(log.duration_ms >= 0 for log in model_logs)
    assert all(log.retry_count == 0 for log in model_logs)

    visible_history = await app_context.client.get(f'/api/chatHistory/app/{app_id}')
    visible_records = visible_history.json()['data']['records']
    assert [record['messageType'] for record in visible_records] == ['ai', 'user']
    assert visible_records[1]['message'] == '生成一个蓝色主题的个人主页'
    assert visible_records[0]['message']


async def test_same_request_id_does_not_create_duplicate_turn_and_can_resume(
    app_context: AppContext,
) -> None:
    _, app_id = await _register_login_and_create_app(app_context)
    payload = {
        'appId': app_id,
        'message': '生成一个页面',
        'requestId': 'turn-idempotent-1',
    }

    first = await app_context.client.post('/api/app/chat/gen/code', json=payload)
    first_events = _parse_sse(first.text)
    resume_after = first_events[1]['id']

    resumed = await app_context.client.post(
        '/api/app/chat/gen/code',
        json=payload,
        headers={'Last-Event-ID': str(resume_after)},
    )
    resumed_events = _parse_sse(resumed.text)

    assert resumed_events
    assert resumed_events[0]['id'] > resume_after
    assert resumed_events[-1]['event'] == 'turn_finished'

    final_id = first_events[-1]['id']
    completed_resume = await app_context.client.post(
        '/api/app/chat/gen/code',
        json=payload,
        headers={'Last-Event-ID': str(final_id)},
    )
    completed_events = _parse_sse(completed_resume.text)
    assert completed_events
    assert completed_events[-1]['event'] == 'turn_finished'
    assert completed_events[-1]['data']['sequence'] == completed_events[-1]['id']

    async with app_context.session_factory() as session:
        user_messages = await session.scalar(
            select(func.count(ChatHistory.id)).where(
                ChatHistory.app_id == app_id,
                ChatHistory.message_kind == 'user',
            )
        )
    assert user_messages == 1


async def test_same_request_id_with_different_message_is_rejected(
    app_context: AppContext,
) -> None:
    _, app_id = await _register_login_and_create_app(app_context)
    base_payload = {
        'appId': app_id,
        'message': '第一条消息',
        'requestId': 'turn-conflict-1',
    }
    first = await app_context.client.post('/api/app/chat/gen/code', json=base_payload)
    assert first.status_code == 200

    conflict = await app_context.client.post(
        '/api/app/chat/gen/code',
        json={**base_payload, 'message': '另一条消息'},
    )
    assert conflict.json()['code'] == 40000


async def test_generation_continues_without_an_active_subscriber(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    manager = app_context.app.state.generation_manager
    job = await manager.start_turn(
        app_id=app_id,
        user_id=user_id,
        code_gen_type='html',
        message='后台继续执行',
        request_id='turn-without-subscriber',
    )

    async with asyncio.timeout(3):
        async with job.condition:
            while not job.completed:
                await job.condition.wait()

    workspace = (
        Path(app_context.app.state.settings.workspace_root) / f'html_{app_id}'
    )
    assert (workspace / 'index.html').exists()
    assert job.events[-1].event == 'turn_finished'


class PathEscapeModel:
    async def next_turn(self, messages: list[dict[str, Any]], on_text: TextEmitter) -> ModelReply:
        if any(message.get('role') == 'tool_result' for message in messages):
            await on_text('越界工具已处理。')
            return ModelReply(text='越界工具已处理。')
        await on_text('尝试越界写入。')
        return ModelReply(
            text='尝试越界写入。',
            tool_calls=[
                ToolCall(
                    id='escape-call',
                    name='write_file',
                    arguments={'path': '../escape.html', 'content': '<h1>escape</h1>'},
                )
            ],
        )


async def test_path_escape_is_reported_as_failed_tool_without_writing(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    model = PathEscapeModel()
    executor = AgentTurnExecutor(
        app_context.session_factory,
        Path(app_context.app.state.settings.workspace_root),
        lambda _: model,
    )
    app_context.app.state.generation_manager = GenerationManager(executor)

    response = await app_context.client.post(
        '/api/app/chat/gen/code',
        json={
            'appId': app_id,
            'message': '尝试越界',
            'requestId': 'turn-path-escape',
        },
    )
    events = _parse_sse(response.text)
    tool_finished = next(event for event in events if event['event'] == 'tool_finished')

    assert tool_finished['data']['status'] == 'failed'
    assert not (Path(app_context.app.state.settings.workspace_root).parent / 'escape.html').exists()
    assert events[-1]['event'] == 'turn_finished'


class FailingModel:
    async def next_turn(self, messages: list[dict[str, Any]], on_text: TextEmitter) -> ModelReply:
        raise RuntimeError('包含内部细节的模型错误')


async def test_model_failure_emits_error_then_failed_turn_without_leaking_detail(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    executor = AgentTurnExecutor(
        app_context.session_factory,
        Path(app_context.app.state.settings.workspace_root),
        lambda _: FailingModel(),
    )
    app_context.app.state.generation_manager = GenerationManager(executor)

    response = await app_context.client.post(
        '/api/app/chat/gen/code',
        json={
            'appId': app_id,
            'message': '触发错误',
            'requestId': 'turn-error',
        },
    )
    events = _parse_sse(response.text)

    assert [event['event'] for event in events[-2:]] == ['error', 'turn_finished']
    assert events[-1]['data']['status'] == 'failed'
    assert '内部细节' not in response.text
    async with app_context.session_factory() as session:
        failed_log = await session.scalar(
            select(ModelCallLog).where(ModelCallLog.turn_id == 'turn-error')
        )
    assert failed_log is not None
    assert failed_log.status == 'error'
    assert failed_log.error_type == 'RuntimeError'
    assert failed_log.error_summary == 'RuntimeError'
    assert failed_log.retry_count == 0


class RecoveringToolModel:
    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(
        self,
        messages: list[dict[str, Any]],
        on_text: TextEmitter,
    ) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            await on_text('第一次工具参数有误。')
            return ModelReply(
                text='第一次工具参数有误。',
                tool_calls=[
                    ToolCall(id='bad-tool', name='write_file', arguments={'path': 'index.html'})
                ],
            )
        tool_result = next(
            message for message in reversed(messages) if message.get('role') == 'tool_result'
        )
        assert tool_result['is_error'] is True
        await on_text('已收到错误并修正。')
        return ModelReply(
            text='已收到错误并修正。',
            tool_calls=[
                ToolCall(
                    id='good-tool',
                    name='write_file',
                    arguments={'path': 'index.html', 'content': '<h1>fixed</h1>'},
                )
            ],
        )


async def test_tool_argument_error_is_returned_to_model_and_recovered(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    executor = AgentTurnExecutor(
        app_context.session_factory,
        Path(app_context.app.state.settings.workspace_root),
        lambda _: RecoveringToolModel(),
    )
    app_context.app.state.generation_manager = GenerationManager(executor)

    response = await app_context.client.post(
        '/api/app/chat/gen/code',
        json={
            'appId': app_id,
            'message': '测试工具参数修复',
            'requestId': 'turn-tool-recovery',
        },
    )
    events = _parse_sse(response.text)
    finished = [event for event in events if event['event'] == 'tool_finished']

    assert [event['data']['status'] for event in finished] == ['failed', 'completed']
    workspace = Path(app_context.app.state.settings.workspace_root) / f'html_{app_id}'
    assert (workspace / 'index.html').read_text(encoding='utf-8') == '<h1>fixed</h1>'


async def test_unavailable_model_returns_readable_failed_turn(
    app_context: AppContext,
) -> None:
    user_id, app_id = await _register_login_and_create_app(app_context)
    executor = AgentTurnExecutor(
        app_context.session_factory,
        Path(app_context.app.state.settings.workspace_root),
        lambda _: UnavailableAgentModel(),
    )
    app_context.app.state.generation_manager = GenerationManager(executor)

    response = await app_context.client.post(
        '/api/app/chat/gen/code',
        json={
            'appId': app_id,
            'message': '缺少模型密钥',
            'requestId': 'turn-no-key',
        },
    )
    events = _parse_sse(response.text)

    assert [event['event'] for event in events[-2:]] == ['error', 'turn_finished']
    assert events[-1]['data']['status'] == 'failed'
    assert 'ANTHROPIC_API_KEY' not in response.text


async def test_multi_file_generation_writes_three_files_and_previews(
    app_context: AppContext,
) -> None:
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(
        CodeGenType.MULTI_FILE
    )
    app_context.app.state.generation_manager = GenerationManager(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: ScriptedMultiFileModel(),
        )
    )
    await app_context.client.post(
        '/api/user/register',
        json={
            'userAccount': 'multi-user',
            'userPassword': 'password123',
            'checkPassword': 'password123',
        },
    )
    await app_context.client.post(
        '/api/user/login',
        json={'userAccount': 'multi-user', 'userPassword': 'password123'},
    )
    created = await app_context.client.post(
        '/api/app/create',
        json={'initPrompt': '创建一个多文件网站'},
    )
    app_id = created.json()['data']

    response = await app_context.client.post(
        '/api/app/chat/gen/code',
        json={
            'appId': app_id,
            'message': '创建多文件页面',
            'requestId': 'turn-multi-file',
        },
    )
    events = _parse_sse(response.text)
    finished = [event for event in events if event['event'] == 'tool_finished']

    workspace = (
        Path(app_context.app.state.settings.workspace_root) / f'multi_file_{app_id}'
    )
    assert {path.name for path in workspace.iterdir()} == {
        'index.html',
        'style.css',
        'script.js',
    }
    assert len(finished) == 3
    assert all(event['data']['status'] == 'completed' for event in finished)
    event_types = [event['event'] for event in events]
    assert 'text_delta' in event_types
    assert event_types[-1] == 'turn_finished'
    preview = await app_context.client.get(
        f'/api/static/multi_file_{app_id}/index.html'
    )
    assert preview.status_code == 200
    html = preview.text
    assert 'style.css' in html
    assert 'script.js' in html
    assert (
        await app_context.client.get(f'/api/static/multi_file_{app_id}/style.css')
    ).status_code == 200
    assert (
        await app_context.client.get(f'/api/static/multi_file_{app_id}/script.js')
    ).status_code == 200
    async with app_context.session_factory() as session:
        tool_rows = await session.scalar(
            select(func.count(ChatHistory.id)).where(
                ChatHistory.app_id == app_id,
                ChatHistory.message_kind == 'tool_call',
            )
        )
    assert tool_rows == 3


class MultiFileEditModel:
    async def next_turn(
        self,
        messages: list[dict[str, Any]],
        on_text: TextEmitter,
    ) -> ModelReply:
        has_tool_result = any(message.get('role') == 'tool_result' for message in messages)
        prompt = str(messages[0].get('content', ''))
        if has_tool_result:
            await on_text('处理完成。')
            return ModelReply(text='处理完成。')
        if '修改' in prompt:
            await on_text('修改既有 HTML 标题。')
            return ModelReply(
                text='修改既有 HTML 标题。',
                tool_calls=[
                    ToolCall(
                        id='edit-title',
                        name='edit_file',
                        arguments={
                            'path': 'index.html',
                            'old_text': '<h1>Multi File</h1>',
                            'new_text': '<h1>Updated Multi File</h1>',
                        },
                    )
                ],
            )
        return await ScriptedMultiFileModel().next_turn(messages, on_text)


async def test_multi_file_follow_up_edits_existing_files(
    app_context: AppContext,
) -> None:
    app_context.app.state.code_gen_type_router = StaticCodeGenTypeRouter(
        CodeGenType.MULTI_FILE
    )
    model = MultiFileEditModel()
    app_context.app.state.generation_manager = GenerationManager(
        AgentTurnExecutor(
            app_context.session_factory,
            Path(app_context.app.state.settings.workspace_root),
            lambda _: model,
        )
    )
    await app_context.client.post(
        '/api/user/register',
        json={
            'userAccount': 'multi-edit-user',
            'userPassword': 'password123',
            'checkPassword': 'password123',
        },
    )
    await app_context.client.post(
        '/api/user/login',
        json={'userAccount': 'multi-edit-user', 'userPassword': 'password123'},
    )
    app_id = (
        await app_context.client.post(
            '/api/app/create',
            json={'initPrompt': '创建多文件页面'},
        )
    ).json()['data']
    await app_context.client.post(
        '/api/app/chat/gen/code',
        json={'appId': app_id, 'message': '初始页面', 'requestId': 'multi-turn-1'},
    )

    await app_context.client.post(
        '/api/app/chat/gen/code',
        json={'appId': app_id, 'message': '修改标题', 'requestId': 'multi-turn-2'},
    )

    workspace = (
        Path(app_context.app.state.settings.workspace_root) / f'multi_file_{app_id}'
    )
    html = (workspace / 'index.html').read_text(encoding='utf-8')
    assert '<h1>Updated Multi File</h1>' in html
    assert (workspace / 'style.css').exists()
    assert (workspace / 'script.js').exists()
