---
policy_id: PAY-COUPON-107
title: Demo Coupon And Pricing Policy / 优惠券价格
version: demo-v1
effective_date: '2026-09-01'
status: active
source_type: demo_policy
is_demo: true
applies_to: Pricing and promotion inquiries for direct Demo orders
---

# 优惠券、促销与价格差

## 优惠券核实

优惠是否适用取决于活动有效期、渠道、商品范围、门槛与叠加条件；结算页面或已核实活动条款才是依据。当前没有活动明细、用券记录或核销工具，不能猜测满减门槛、生成可用券码或承诺补券。

## 订单金额

查询得到的 total_cents 与 currency 是订单记录的总额，不证明优惠券金额、税费分摊或支付已完成。不要用商品单价差额自行推导折扣，也不要擅自混用美元、英镑和人民币。

## 降价与退货

购买后降价不自动享有保价。本 Demo 没有统一保价期限或补差规则，须核实适用活动。退货后的优惠券返还及赠品处理亦须核实，不承诺恢复优惠券。促销本身不改变 14×24 小时退货申请窗口，也不证明商品为不可退的最终销售商品。

## 示例

“今天便宜了二十，直接退我差价”：可解释待核实事项，但当前不能执行价保退款。
