# 合同审查 Mini · AI Coding 全栈笔试训练

在原 Mini Echo / EventInsight 练习上扩展：上传合同 → 配置规则审查 → 人工复核 → 保存审计 → 导出报告。

技术栈保持 React + JavaScript/JSX + Vite + CSS、Python + FastAPI + Pydantic + Uvicorn；持久化使用 Python 内置 SQLite。无需 PostgreSQL、Docker、模型密钥或额外付费服务。

## 启动（Windows PowerShell，两个终端）

后端终端：

```powershell
cd "C:\Users\User\antigravityProject\ai-fullstack-training\project1\backend"
# 仅首次、且还没有 .venv 时执行：py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

前端终端：

```powershell
cd "C:\Users\User\antigravityProject\ai-fullstack-training\project1\frontend"
npm.cmd ci
npm.cmd run dev -- --host localhost --port 5173 --strictPort
```

浏览器打开 <http://localhost:5173/>；接口文档为 <http://127.0.0.1:8000/docs>。已有对应服务运行时不要重复启动。换电脑后请调整项目绝对路径。

前端默认连接 `http://127.0.0.1:8000`。改端口可在启动 Vite 前设置 `$env:VITE_API_BASE_URL = "http://127.0.0.1:其他端口"`；前端来源改变时也要调整后端 CORS 白名单。

## 演示流程

1. 上传 `samples/pass.txt`（UTF-8），点击开始审查：应有 4 条通过、0 条风险。
2. 上传 `samples/risk.txt`：应有 2 条风险（R001、R002），其余通过。
3. 点击命中证据，查看对应原文位置。缺失类规则没有命中证据，会明确说明未找到内容。
4. 填写操作人，为一条结果选择“通过 / 风险 / 不适用”，填写原因后保存。
5. 审计记录展示操作人、时间、规则、原状态、新状态和原因；初始机器判断不会被覆盖。
6. 导出 JSON 或 Markdown，报告包含合同原文、规则结果、原始与最终状态及审计记录。
7. 刷新页面，从历史记录恢复审查；重新启动后端也不会丢失已经保存的数据。

页面只支持 UTF-8 TXT（最大 1 MiB，合同最多 100000 字符），也可直接编辑文本或载入内置样例。不支持 PDF、Word、图片/OCR。输入框中的草稿只有重新审查后才成为新记录；报告基于已保存的审查，不包含尚未保存的人工修改。

## 文件与逻辑

```text
project1/
  backend/
    main.py                FastAPI 路由、CORS、旧练习接口
    schemas.py             请求与响应的数据结构、输入校验
    rule_engine.py         配置读取、关键词/正则匹配、证据定位
    rules.json             四条规则，新增规则只改本文件
    storage.py             SQLite 审查记录和审计持久化
    analyzer.py            保留原事件分析练习
    requirements.txt       后端运行依赖
    requirements-dev.txt   后端测试依赖
    tests/                 独立临时数据库测试
    data/                  运行时数据库，已加入 .gitignore
  frontend/
    src/                   React 页面、接口调用、样式、原练习
  samples/
    pass.txt               全部通过
    risk.txt               恰好两条风险
```

```text
React 读取 TXT → fetch 发送 JSON → FastAPI 校验
  → rule_engine 读取 rules.json → 匹配并提取证据 → SQLite 保存
  → 返回结果 → React 显示

人工修改 → FastAPI → 同一事务中更新最终状态并写审计
导出请求 → 从 SQLite 读取已保存结果与审计 → 下载报告
```

## API

| 方法和路径 | 请求 / 用途 |
| --- | --- |
| `POST /review` | `{ "filename": "合同.txt", "content": "合同原文" }`，返回完整审查 |
| `GET /reviews` | 返回最近 20 份审查摘要 |
| `GET /reviews/{review_id}` | 返回已保存原文、结果、审计 |
| `PATCH /reviews/{review_id}/results/{rule_id}` | `{ "status": "pass", "actor": "张三", "reason": "已人工核实", "expected_status": "risk" }` |
| `POST /report` | `{ "review_id": "审查ID", "format": "json" }`，或 `format: "markdown"` |

状态值：`pass`（通过）、`risk`（风险）、`not_applicable`（不适用）。422 表示输入无效或状态未变化；404 表示记录不存在；409 表示其他操作已经修改了结果，应重新载入后再提交。

原 `GET /health`、`POST /api/echo`、`POST /api/analyze-preview` 继续保留。

## 新增规则

在 `backend/rules.json` 中追加规则对象，不用修改 Python 代码。例如：

```json
{
  "id": "R005",
  "name": "检查测试风险标记",
  "description": "训练演示：发现测试风险标记时提醒复核。",
  "type": "keyword",
  "pattern": "测试风险标记",
  "risk_when": "matched"
}
```

`type` 支持 `keyword` 和 `regex`；`risk_when` 支持 `matched`（命中是风险）和 `missing`（未命中是风险）。每次审查重新读取配置，新配置只影响新审查，不追溯修改历史结果。正则由开发者维护，不开放给上传者；请避免灾难性回溯的正则表达式。

默认规则：R001 单方解除表述、R002 免责表述、R003 待填占位符、R004 是否包含“争议解决”。这些是简单确定性示例，不是法律判断。

## 数据与安全边界

- 默认数据库为 `backend/data/reviews.sqlite3`；可用 `CONTRACT_DB_PATH` 环境变量指定其他路径。
- 数据库会保存合同全文。不要提交真实合同、数据库、`.env`、`.venv` 或 `node_modules`；`.gitignore` 已排除运行数据和常见敏感文件。
- 人工修改和审计写入同一事务；使用 `expected_status` 检测过期提交，避免静默覆盖。
- 操作人是用户自填的演示身份，没有认证、权限系统或防篡改审计。仅限本地训练，不要直接作为公网服务部署。
- SQLite 文件不是备份；需要保留历史时请自行备份，勿删除 `backend/data`。
- 没有调用 LLM，合同不会由本项目发给外部模型。所有规则结果仅供演示、需人工复核，不构成法律意见。

## 自动化检查

```powershell
cd "C:\Users\User\antigravityProject\ai-fullstack-training\project1\backend"
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v

cd "C:\Users\User\antigravityProject\ai-fullstack-training\project1\frontend"
npm.cmd run build
```

测试使用临时数据库，不会覆盖默认数据库。覆盖规则匹配、样例、输入校验、人工修改、审计持久化、冲突处理、报告和旧接口。前端 build 检查可构建性，不能替代浏览器交互验收。
