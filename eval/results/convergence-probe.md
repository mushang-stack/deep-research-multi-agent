# Researcher 收敛三臂定向复现实验

- 每臂 × 2 子问题 × 10 次,温度 0.3(随机复发需统计样本)
- 臂:A=基线 / B=纯截断 6000 / C=修复后(F1 预告 + F2 max_steps partial)

| 臂 | n | 0-findings 率 | findings 均值 | hit_max | escalated | degraded | mean_steps | errors |
|---|---|---|---|---|---|---|---|---|
| A | 20 | 10% | 8.2 | 2 | 2 | 0 | 4.9 | 0 |
| B | 20 | 15% | 8.0 | 3 | 3 | 0 | 5.1 | 0 |
| C | 20 | 0% | 8.9 | 0 | 0 | 0 | 5.1 | 0 |

判读:C 臂 0-findings 率应为 0 或明显低于 A(方向性证据;每臂 n=20 不做显著性检验);B−A 差值 = 截断放大效应(如实记录方向)。errors=API 抖动等基础设施失败,已从比率分母剔除。
耗尽读法:A/B 臂看 hit_max;C 臂救援不记 hit_max,看 degraded(其耗尽被收尾救回)。
