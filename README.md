# mcd-value-calc · 麦当劳性价比计算器

把「菜单价格」和「官方营养数据」这两套从来没人放在一起算的官方数据对起来，回答两个问题：

- **同样的钱，买什么蛋白质最多？**
- **给定预算（如 ¥30），怎么组合蛋白质最多？**

一条命令跑完，产出 Markdown 榜单和单文件 HTML 报告。**只读数据，不下单。**

## 它算了什么

每个商品三个指标：

| 指标 | 口径 |
|---|---|
| 一块钱买多少蛋白质 | 蛋白质(g) ÷ 成交价 |
| 一块钱买多少热量 | 热量(kcal) ÷ 成交价 |
| 蛋白质供能比 | 蛋白质(g) × 4 ÷ 总热量(kcal) |

外加：**预算组合**——把预算按 0.1 元离散化后跑整数背包，得到全局最优的蛋白质组合（不是贪心近似）。

## 三个较真的地方（本项目和同类工具的区别）

1. **价格用官方成交价，不用菜单标价。** 菜单上的 `currentPrice` 常是「早餐卡专享」「随单购」这类带条件的展示价，实测同一份菜单里 18/123 件的展示价低于实际能成交的价格。本项目对每件商品调用官方价格接口取成交价，差价商品在榜单中用 `*` 标记。
2. **匹配不上就老实说，不瞎编。** 菜单名和官方营养表逐条匹配，每条结果带 `matched_by` 可溯源；新品、统称商品（如不写杯型的「玉米杯」）一律标「未匹配」并注明原因，**不估算、不插值、不用同类商品顶替**，并给出总匹配率。
3. **预算组合是精确解。** 0.1 元离散化的整数背包动态规划，不是「多买几个 McFlurry 试试」的贪心。

## 快速开始

### 1. 准备 MCP Token

按 [麦当劳 MCP Server 使用指南](https://github.com/M-China/mcd-mcp-server) 申请 Token，然后设置环境变量：

```bash
export MCD_MCP_URL="https://mcp.mcd.cn"
export MCD_MCP_TOKEN="你的Token"
```

（也支持从 `~/.workbuddy/mcp.json` 的 `mcd-mcp` 条目读取，供 WorkBuddy 用户复用已有配置。）

### 2. 运行

依赖：Python 3.8+，**仅标准库，无需 pip install**。

```bash
# 指定城市+关键词，自动选最近一家在营业门店
python scripts/mcd_value.py --city 西安 --keyword 高新 --budget 30

# 或直接指定门店 storeCode 复跑
python scripts/mcd_value.py --store 1960652 --budget 30
```

输出在 `cache/` 下：

| 文件 | 内容 |
|---|---|
| `result.md` | 三条榜单 + 预算组合 + 未匹配清单（含逐条原因） |
| `result.json` | 全量结构化数据 |
| `report.html` | 单文件 HTML 报告，深蓝科技风，无外部依赖，内网可直接打开 |

也可以手动重渲染报告：

```bash
python scripts/render_html.py
```

### 3. 作为 WorkBuddy Skill 安装（可选）

把整个目录复制到 `~/.workbuddy/skills/mcd-value-calc`，在 WorkBuddy 里说「麦当劳什么最划算」「30 块怎么吃蛋白最多」即可触发。

## 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--city` / `--keyword` | 西安 / 钟楼 | 门店搜索条件 |
| `--store` | 无 | 直接指定 storeCode，跳过门店搜索 |
| `--budget` | 30 | 预算金额（元） |
| `--top` | 15 | 每个榜取前几名 |
| `--be-type` / `--order-type` | 1 / 1 | 1=到店自取；也支持 5 得来速、2 麦乐送、6 团餐 |
| `--price-basis` | actual | `actual` 官方成交价；`menu` 菜单展示价（快，但含条件价失真） |
| `--once` | 关 | 预算组合每样限一份（默认允许重复购买） |
| `--refresh` | 关 | 忽略缓存重拉 |

## 示例输出

见 [`example/`](example/)：某门店正餐时段实测——123 件在售商品，成交价 123/123 取到，营养匹配率 50.4%（62/123），¥30 预算最优解为 2×麦麦脆汁鸡 = 42g 蛋白。

## 目标用户

- 健身/控卡人群：同样预算下蛋白拉满
- 精打细算的打工人：一块钱到底能买到什么
- 对「数据对不上就标未知」有洁念的人

## 已知限制（照实说）

- 菜单随时段变（早餐/正餐切换），营养表滞后于新品，新品只能进未匹配清单
- 套餐营养 = 官方默认组成之和，实际换杯型/加料会变
- 蛋白供能比榜里黑咖啡会因热量极低排名虚高，请结合绝对蛋白量看

## 参赛说明

本项目为 [麦当劳程序员创意开发大赛](https://github.com/M-China/mcd-developer-innovation-challenge) 参赛作品，使用 [WorkBuddy](https://www.workbuddy.cn/) 辅助开发。参赛声明见 [CONTEST_DECLARATION.md](CONTEST_DECLARATION.md)，MCP 接入说明见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)。

## License

MIT
