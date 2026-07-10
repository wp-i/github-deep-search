# 调研结论

## 一句话判断
已整理 3 个相邻方向，最接近的是 shopware/shopware（15/100），目前更适合用来找灵感，不适合直接采用。

## 已整理的线索

### 1. shopware/shopware（相邻参考） · ★ 3382 · 更新 2026-07-10
- 关联度：15/100
- 得分原因：公开证据只支持较弱相邻关系；项目定位或使用方式也有差异；该项目可核对线索：Shopware 6 is an open commerce platform based on Symfony Framework and Vue and suppo

...[truncated]...

100 community extensions。
- 差异部分：需求：给定一条商品链接，读取价格和优惠明细，计算最终到手价；项目：Shopware是一个完整的电商平台，用于搭建和管理在线商店，包含商品管理、购物车、结账等完整功能，但不提供从外部商品链接解析价格和优惠的功能
- 地址：https://github.com/shopware/shopware

### 2. projazzka/price-calc（相邻参考） · ★ 1 · 更新 2011-12-05
- 关联度：15/100
- 得分原因：公开证据只支持较弱相邻关系；项目定位或使用方式也有差异；该项目可核对线索：Wordpress Price Calculator plugin。
- 差异部分：需求：给定一条商品链接，读取价格和优惠明细，计算最终到手价；项目：WordPress插件，允许商家创建自定义价格计算表单，用户手动选择选项后计算报价，不涉及外部链接解析
- 地址：https://github.com/projazzka/price-calc

### 3. aimadetools/race-xiaomi（相邻参考） · ★ 0 · 更新 2026-07-10
- 关联度：15/100
- 得分原因：公开证据只支持较弱相邻关系；项目定位或使用方式也有差异；该项目可核对线索：github_issue:calculate final price from URL。
- 差异部分：需求：给定一条商品链接，读取价格和优惠明细，计算最终到手价；项目：AI API成本计算器，比较不同LLM模型的价格，与电商商品无关
- 地址：https://github.com/aimadetools/race-xiaomi

## 本次消耗
- LLM Token：输入 35968，输出 5157，合计 41125。