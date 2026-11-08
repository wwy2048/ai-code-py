/* eslint-disable */
import request from '@/request'

/** 管理员按用户和应用聚合模型调用消费 */
export async function listModelCallConsumptionByPageForAdmin(
  body: API.ModelCallConsumptionQueryRequest,
  options?: { [key: string]: any },
) {
  return request<API.BaseResponsePageModelCallConsumption>(
    '/modelCall/admin/consumption/page',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      data: body,
      ...(options || {}),
    },
  )
}
