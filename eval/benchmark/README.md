# 基准题库

每道题一个 `.yaml` 文件,供 `python -m eval.run_eval` 逐题深研 + GLM 裁判 + 算 4 质量指标。

## 格式

```yaml
id: q001                          # 必填,唯一,用作结果文件名
question: "一句话研究问题"          # 必填,系统将深研它
key_facts:                         # 必填,非空,每条是可被报告覆盖的可核查事实
  - "关键事实 1"
  - "关键事实 2"
```

## 规则

- 文件名以 `_` 开头的(如 `_template.yaml`)会被 runner **跳过**——用作模板,不计入评估。
- 缺 `id` / `question` / `key_facts`(或 key_facts 为空 / 含非字符串)的题会被 runner **跳过并告警**,不中断全集。
- `key_facts` 是**人写的金标准**:其质量直接决定覆盖率指标上限,务必写具体、可核查的事实,而非泛泛的方向。
- 建议种子题控制在 5 道左右,以控制真跑成本(每题 = 一次完整研究 + N+M 次 GLM 裁判)。

## 跑

```bash
python -m eval.run_eval                 # 跑全集
python -m eval.run_eval --limit 1       # 只跑第一题
python -m eval.run_eval --only q001     # 只跑指定 id
python -m eval.run_eval --benchmark DIR # 指定题库目录(默认 eval/benchmark)
python -m eval.run_eval --results DIR   # 指定结果输出目录(默认 eval/results)
```

结果写到 `eval/results/<id>.json` 与 `eval/results/scorecard.json`(已 gitignore)。
