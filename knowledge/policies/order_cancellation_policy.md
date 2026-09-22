---
policy_id: ORD-CANCEL-101
title: Demo Order Cancellation Policy / 订单取消
version: demo-v1
effective_date: '2026-09-01'
status: active
source_type: demo_policy
is_demo: true
applies_to: Direct Demo orders before shipment
---

# 订单取消与拦截

## 发货前

本模拟商城允许未发货订单提出取消请求，但受理不等于取消成功。confirmed 只表示订单已确认，不证明仓库未拣货或可拦截；还须核实履约进度。当前工具只能查询订单，不能取消、拦截或核实仓库任务。

## 发货后

shipped 或 delivered 订单不按发货前取消处理。承运商拦截须经授权物流流程核实，不保证成功；不可直接建议拒收来获得退款。已送达商品可按退货政策检查，申请期限仍为记录签收起精确 14×24 小时。

## 取消与资金

cancelled 状态不证明退款已发起或到账。银行卡预授权撤销与已扣款退款不同，须核实支付记录；当前订单数据不能证明扣款状态。不可承诺到账日期、取消手续费或已为客户取消。

## 示例

“刚买错了，帮我取消”：先定位本人订单，解释需核实的履约状态；没有执行工具时只提供说明，不声称操作成功。
