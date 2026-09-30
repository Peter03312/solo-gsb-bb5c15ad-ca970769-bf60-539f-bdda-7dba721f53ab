# 临时活动无线电使用申请 API

面向临时活动的无线电频率使用申请服务。申请需写明：

- **覆盖区域**：平面坐标中的一个圆（圆心 `center_x` / `center_y` 与正半径 `radius`，同一长度单位）；
- **频率范围**：闭区间 `[freq_low_mhz, freq_high_mhz]`（MHz）；
- **使用时段**：UTC 左闭右开区间 `[start_utc, end_utc)`（ISO-8601，建议带 `Z`）。

技术栈：**Python 3.11 + FastAPI + PostgreSQL 16 + SQLAlchemy 2 + psycopg v3**。

---

## 1. 一键启动

需要 Docker 与 Docker Compose v2：

```bash
docker compose up -d --build
```

该命令会：

1. 启动 PostgreSQL 16（健康检查通过后才启动 API）；
2. 构建 API 镜像；
3. API 启动时自动等待数据库并**幂等建表初始化**（`applications`、`event_log`）；
4. 监听 **http://localhost:8000**。

重置数据：

```bash
docker compose down -v        # 删除数据库卷
docker compose up -d --build
```

| 地址 | 说明 |
| --- | --- |
| http://localhost:8000 | API 根 |
| http://localhost:8000/health | 健康检查 |
| http://localhost:8000/docs | Swagger UI（可直接交互调用） |
| http://localhost:8000/openapi.json | OpenAPI 描述 |
| `localhost:5432` | PostgreSQL（库/用户/密码：`radio`/`radio`/`radio`） |

跑通完整调用示例（提交、预览、乐观裁决、冲突、撤销、历史还原）：

```bash
sh scripts/demo.sh
```

---

## 2. 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://radio:radio@db:5432/radio` | SQLAlchemy/psycopg v3 连接串 |
| `GUARD_BAND_MHZ` | `0.0`（compose 中设为 `0.25`） | 服务端默认保护带宽（MHz），申请未给 `guard_band_mhz` 时使用；非负 |
| `DB_CONNECT_RETRIES` | `30` | 启动等待数据库的最大重试次数 |
| `DB_CONNECT_RETRY_INTERVAL_SECONDS` | `2.0` | 重试间隔秒数 |

本地裸机运行（需自行提供 PostgreSQL）：

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg://radio:radio@localhost:5432/radio"
uvicorn app.main:app --reload
```

---

## 3. 业务规则

### 3.1 申请校验（整笔拒收，HTTP 422，不产生记录）

- 半径非正（`radius <= 0`）；
- 频段下界大于上界（`freq_low_mhz > freq_high_mhz`）；
- 时间起点不早于终点（`start_utc >= end_utc`，半开区间要求严格小于）；
- `guard_band_mhz` 为负（Pydantic 层拒绝）。

### 3.2 两项已批准申请构成干扰的条件

必须**同时**满足：

1. **时段相交**：左闭右开区间，`start1 < end2 且 start2 < end1`（端点相接不算）；
2. **覆盖区域相交**：圆心距 `< r1 + r2`（外切不算）；
3. **频率冲突**，二者满足其一：
   - 闭频段相交：`low1 <= high2 且 low2 <= high1`（端点相接算相交，`trigger=band_overlap`）；
   - 频段不相交但**最短间隔**（两区间净空）**小于**保护带宽：
     `gap < required_guard_band_mhz`（严格小于；相等不冲突，`trigger=guard_band`）。

保护带宽取两申请各自要求值的**较大者**（申请可通过 `guard_band_mhz` 单独指定，否则取服务端默认值）。

> 已撤销的申请不在“有效集合”中，不参与后续裁决。

### 3.3 预览、批准与乐观并发控制

- 每次批准/撤销都会产生一条事件，其自增 id 即全局单调的**修订号（revision）**，无事件时为 0。
- `GET .../preview` 返回当前修订号 `current_revision`，以及冲突对象列表和逐项触发条件。
- `POST .../approve` 必须携带 `expected_revision`。服务端在**事务级 PostgreSQL 咨询锁**内串行裁决：
  - 修订号与当前不一致 → **409 `STALE_REVISION`**（期间已有并发批准/撤销落库，须重新预览）；
  - 重新裁决发现与当前有效集合冲突 → **409 `CONFLICT`**（响应体列出冲突对象与触发条件，不占用频段）；
  - 申请已被处理/撤销 → **409 `INVALID_STATE`**；
  - 全部通过 → 写入批准事件并返回新修订号。
- 因为“读修订号 + 裁决 + 写事件”在同一把锁的事务内完成，过期修订或与并发批准相撞的申请都不可能占用频段。

### 3.4 撤销与历史还原

- `POST .../revoke` 仅把申请移出后续裁决的有效集合，并追加一条修订事件；
- `GET /api/revisions/{n}` 重放截至第 `n` 号的批准/撤销事件流，**还原当时**的有效批准集合与全部事件记录；
- `GET /api/revisions` 返回当前修订号与完整修订流。

---

## 4. HTTP API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/applications` | 提交申请（非法整笔拒收 422） |
| `GET` | `/api/applications` | 列出全部申请 |
| `GET` | `/api/applications/{id}` | 查询单笔申请 |
| `GET` | `/api/applications/{id}/preview` | 冲突预览（冲突对象 + 触发条件 + 当前修订号） |
| `POST` | `/api/applications/{id}/approve` | 携带修订号批准（重新裁决） |
| `POST` | `/api/applications/{id}/revoke` | 撤销已批准申请 |
| `GET` | `/api/revisions` | 当前修订号与修订流 |
| `GET` | `/api/revisions/{n}` | 按修订号还原当时的批准/撤销状态 |
| `GET` | `/health` | 健康检查 |

错误响应统一为：

```json
{ "error": "STALE_REVISION", "message": "...", "expected_revision": 0, "current_revision": 1 }
```

---

## 5. 调用示例（curl）

### 5.1 提交申请

```bash
curl -sS http://localhost:8000/api/applications \
  -H 'Content-Type: application/json' \
  -d '{
    "center_x": 0, "center_y": 0, "radius": 1000,
    "freq_low_mhz": 450.00, "freq_high_mhz": 452.00,
    "guard_band_mhz": 0.25,
    "start_utc": "2026-10-05T10:00:00Z",
    "end_utc":   "2026-10-05T12:00:00Z",
    "applicant": "Alpha",
    "purpose": "concert main"
  }'
```

返回 `201`：

```json
{
  "id": 1, "center_x": 0.0, "center_y": 0.0, "radius": 1000.0,
  "freq_low_mhz": 450.0, "freq_high_mhz": 452.0, "guard_band_mhz": 0.25,
  "start_utc": "2026-10-05T10:00:00Z", "end_utc": "2026-10-05T12:00:00Z",
  "applicant": "Alpha", "purpose": "concert main",
  "status": "pending", "created_at": "...", "decided_at": null
}
```

非法申请被整笔拒收（示例：下界大于上界），返回 `422`：

```bash
curl -i http://localhost:8000/api/applications \
  -H 'Content-Type: application/json' \
  -d '{"center_x":0,"center_y":0,"radius":1000,
       "freq_low_mhz":460,"freq_high_mhz":450,
       "start_utc":"2026-10-05T10:00:00Z","end_utc":"2026-10-05T12:00:00Z"}'
```

### 5.2 冲突预览

```bash
curl -sS http://localhost:8000/api/applications/1/preview
```

无冲突时：

```json
{ "application_id": 1, "current_revision": 0, "has_conflict": false, "conflicts": [] }
```

存在冲突时（另一申请在同时段、同区域使用了相交频段）：

```json
{
  "application_id": 2,
  "current_revision": 1,
  "has_conflict": true,
  "conflicts": [
    {
      "application": { "id": 1, "status": "approved", "...": "..." },
      "time_overlap": true,
      "area_overlap": true,
      "frequency_overlap": true,
      "band_gap_mhz": null,
      "required_guard_band_mhz": 0.25,
      "trigger": "band_overlap"
    }
  ]
}
```

频段不相交但净空小于保护带宽时，`trigger` 为 `guard_band`，并给出
`band_gap_mhz`（如 `0.2 < 0.25`）。

### 5.3 携带修订号批准

```bash
curl -sS -X POST http://localhost:8000/api/applications/1/approve \
  -H 'Content-Type: application/json' \
  -d '{"expected_revision": 0}'
```

```json
{ "application_id": 1, "status": "approved", "revision": 1 }
```

过期修订（期间已有其他裁决落库）：

```json
{
  "error": "STALE_REVISION",
  "message": "expected revision is outdated; re-fetch preview and arbitrate again",
  "expected_revision": 0,
  "current_revision": 1
}
```

与并发批准相撞（修订号是最新的，但重新裁决仍冲突）：

```json
{
  "error": "CONFLICT",
  "message": "application conflicts with currently approved applications; cannot occupy spectrum",
  "application_id": 2,
  "conflicts": [ { "application": { "id": 1, "...": "..." }, "trigger": "band_overlap" } ]
}
```

两种情况下申请都保持 `pending`，可在冲突解除或参数修改后重新预览、批准。

### 5.4 撤销

```bash
curl -sS -X POST http://localhost:8000/api/applications/1/revoke
# { "application_id": 1, "status": "revoked", "revision": 3 }
```

撤销后该申请立即退出后续裁决的有效集合；对非 `approved` 状态调用返回 `409 INVALID_STATE`。

### 5.5 按修订号还原历史

```bash
curl -sS http://localhost:8000/api/revisions/1
```

```json
{
  "revision": 1,
  "approved": [ { "id": 1, "status": "approved", "...": "..." } ],
  "events": [
    { "revision": 1, "application_id": 1, "event_type": "approved", "created_at": "..." }
  ]
}
```

撤销发生后的修订号（如 3）会显示：有效批准集合中已不含被撤销申请，但 `events` 同时保留批准与撤销两条记录。查询未来修订号返回 `404 REVISION_NOT_FOUND`。

---

## 6. 目录结构

```
app/
  main.py           # FastAPI 入口、生命周期（等库 + 建表）
  config.py         # 环境变量配置
  database.py       # 引擎、会话、等待/初始化
  models.py         # applications / event_log 表模型
  schemas.py        # 请求/响应模型与整笔校验
  interference.py   # 纯函数：时间/圆/频段相交与保护带宽裁决
  service.py        # 修订号、有效集合、冲突构造、快照重放、咨询锁键
  routes.py         # HTTP 路由
scripts/demo.sh     # 端到端调用示例
Dockerfile
docker-compose.yml
```
