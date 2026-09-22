---
policy_id: SHIP-DELAY-103
title: Demo Shipment Delay Policy / 物流延误
version: demo-v1
effective_date: '2026-09-01'
status: active
source_type: demo_policy
is_demo: true
applies_to: Outbound shipment delays for direct Demo orders
---

# 物流停滞与预计送达

## 查询与解释

in_transit 表示运输中，out_for_delivery 表示派送中，都不证明已经送达。扫描长时间不更新可能涉及中转、天气、节假日或漏扫，但未核实的原因只能作为可能性。预计送达时间不是保证，不能自行补出“今晚一定到”。

## 延误处理

超过已核实的预计送达时间，或扫描异常持续，可提出承运商调查请求；缺少预计时间时不能自行认定超时赔付。当前没有调查建单、催件或赔偿工具，也没有统一的“停滞多少小时算丢件”规则。

## 退货期限

未送达订单不因下单时间久而自动进入退货申请期。送达后的申请期限以业务记录签收时间计算，精确 14×24 小时；不能用物流页面刷新时间替代。

## 数据边界

当前 track_shipment 返回模拟出库物流快照，不连接实时承运商。必须说明数据性质，不把快照称为当前实时位置，也不声称客服已催件。
