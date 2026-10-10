# API 参考

MusicPlayer 后端（FastAPI）接口文档。基础地址：`https://music.example.com` 或 `http://127.0.0.1:8080`。

## 认证

网页端默认使用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录，并在登录前填写一次性图片验证码。登录成功后服务器设置 HttpOnly、SameSite=Lax 的签名会话 Cookie，默认有效 30 天；连续登录失败会触发按 IP 限流。

无需登录的端点：`/api/health`、`/api/auth/status`、`/api/auth/captcha`、`/api/auth/login`、`/api/auth/logout`。其余 `/api/*` 需要有效会话。

登录接口：

```
GET  /api/auth/status   → {enabled, authenticated, username}
GET  /api/auth/captcha  → {id, image, expires_in}   // image 为 data URL，5 分钟有效
POST /api/auth/login    {username,password,captcha_id,captcha}
POST /api/auth/logout
GET  /api/auth/access-audit → 成功登录 IP 数、属地、登录/最近访问时间和分钟级活跃记录数
```

`API_TOKEN` 保留给脚本、Navidrome 之外的旧客户端和应急管理。设置后可用以下任一方式代替网页登录会话：

```http
X-Api-Token: <token>
```

或查询参数（用于 `<audio>` 等无法自定义请求头的场景）：

```
/api/stream/1?token=<token>
```

未认证返回 `401 {"detail": "请先登录"}`。查询参数令牌仅应用于 `<audio>`、封面、CSV 等无法添加自定义请求头的兼容场景，不应出现在普通网页链接中。

## 数据结构

### Song

```jsonc
{
  "id": 149,
  "bvid": "BV1Z1421U7rD",              // B站视频号（与 part_index 联合唯一）
  "job_id": "241f2992fb5f",
  "title": "カタオモイ",                 // 歌曲名（可编辑）
  "artist": "单相思",                   // 歌手（可编辑）
  "album": "musicplayer",              // 专辑（可编辑）
  "duration": 205.0,                   // 选中分P的时长（秒）
  "cid": 123456789,                    // 选中分P的 cid
  "part_index": 1,                     // 选中第几P
  "part_title": "P1",
  "parts": [                           // 全部分P
    { "cid": 123, "page": 1, "part": "【xx】海屿你", "duration": 296 }
  ],
  "source_url": "https://www.bilibili.com/video/BV1Z1421U7rD?p=1",  // B站源链接
  "downloaded_cid": 123456789,         // 实际下载过的分P（去重用）
  "raw_title": "原标题",
  "uploader": "呜米",
  "tags": ["翻唱", "Aimer"],
  "cover_url": "…", "cover": "/api/songs/149/cover",
  "file_path": "musicplayer/单相思 - カタオモイ [BV1Z1421U7rD].m4a",
  "lyrics": "纯文本歌词", "lrc": "[00:00.00]…", "lyrics_source": "lrclib",
  "lyrics_enabled": true,
  "download_requested": null,           // 多P：null 待选择 / 0 跳过 / 1 下载
  "status": "ready",                   // pending | downloading | ready | error
  "error": null
}
```

### Job

```jsonc
{
  "id": "241f2992fb5f",
  "url": "https://space.bilibili.com/12345678/favlist?fid=1234567890",
  "media_id": "1234567890",
  "title": "musicplayer",
  "status": "parsed",                  // parsed | downloading | done
  "total": 40, "done": 40, "failed": 0,
  "message": "完成：成功 40 首，失败 0 首"
}
```

### SavedCollection

```jsonc
{
  "id": 1,
  "media_id": "1234567890",
  "url": "https://space.bilibili.com/12345678/favlist?fid=1234567890",
  "title": "musicplayer",
  "album": "musicplayer",
  "auto_update": true,              // 每满 24 小时检查一次
  "fetch_lyrics": true,
  "multipart_policy": "ask",          // ask / first / all
  "multipart_count": 1,                // 全部多P视频数（含已处理）
  "pending_multipart_count": 1,        // 待选择的视频数
  "song_count": 160,
  "downloaded_count": 141,
  "last_checked_at": "2026-09-01T06:30:00+00:00",
  "last_error": null
}
```

## 端点

### 健康检查

```
GET /api/health
→ {"ok": true, "service": "musicplayer"}
```

### 导入任务

**解析收藏夹（不下载）**

```
POST /api/jobs
{
  "url": "…",
  "cookie": "SESSDATA=…（可选）",
  "album": "自定义专辑名（可选）",
  "save_collection": true,
  "auto_update": true,
  "multipart_policy": "ask"
}
→ {"job": Job, "songs": [Song…]}
```

已有音频会保留 `ready` 并由前端默认取消勾选；服务端开始任务时还会核对实际文件和分P，避免状态污染导致重下。

错误：`400` 链接格式错误/私密缺 Cookie；`502` 服务器内部错误（含日志）。

**任务列表 / 详情**

```
GET /api/jobs            → [Job…]
GET /api/jobs/{id}       → {"job": Job, "songs": [Song…]}   // 下载进度轮询用
```

**开始下载（确认元数据后）**

```
POST /api/jobs/{id}/start
{ "song_ids": [149, 150], "fetch_lyrics": true }  // 可关闭自动歌词匹配
→ {"started": true, "queued": 500, "concurrency": 3, "message": "已排队 500 首"}
```

所有任务共享同一个下载队列；`concurrency` 默认 3 且服务端硬限制不超过 5。

下载器只使用所选音轨的 `playurl` 原始签名地址：**默认下载备用地址，主地址只作为最后的失败回退**。只有一个备用主机时不测速；有多个备用主机时自动比较其中最多三个，测速结果不会让主地址越过备用地址。探测每个备用主机的第二个 1 MiB（`Range: bytes=1048576-2097151`），避免只测文件头的热缓存而误判整首下载速度；同一备用主机组合的测速结果在进程内缓存五分钟，首次探测串行去重，不额外增加下载线程。短文件返回 416 且提供总长时，只允许一次有界首段重试；200、异常或超范围的 `Content-Range` 不读取响应体。采样预算为六秒，单次网络读取超时两秒。下载失败或所选 CID 时长/容器完整性校验失败会依次尝试其余备用地址，最后使用主地址；主备角色由每次 `playurl` 返回结果决定，不固定指定域名，也不拼造第三方节点或输出签名 URL/Cookie。没有主地址的返回会把所有备用地址正常排序，不把第一个备用误认成主地址。
重复启动正在运行的同一任务不会重复排队。只选择部分歌曲时，任务进度只按本次选择统计。

**删除/取消任务**

```
DELETE /api/jobs/{id}
→ {"deleted": true}
```

取消预览会删除本次新增且尚未下载的记录；重复导入时关联到该任务的既有曲库歌曲会保留并解除任务关联。
正在下载的任务返回 `409`，防止后台线程与删除操作竞态。

### 已保存收藏夹

```
GET    /api/collections                         → [SavedCollection…]
GET    /api/collections/{id}/songs              → 当前仍在该收藏夹内的 [Song…]；?multipart_only=true 仅返回多P供展开列表
PATCH  /api/collections/{id} {"auto_update":true,"multipart_policy":"ask"}
POST   /api/collections/{id}/refresh             → 立即同步，按选择/策略下载并返回 pending_multipart_count
POST   /api/collections/{id}/parts {"song_ids":[149],"bvids":["BV…"]} → 保存所列视频的完整分P选择并下载
DELETE /api/collections/{id}                     → 取消保存，保留歌曲和音频
```

收藏夹与歌曲使用多对多关系，同一首歌属于多个收藏夹也只保存一份音频。自动更新进程每 15 分钟扫描一次，但每个收藏夹必须距上次检查满 24 小时才会执行；同步只请求新增或缺少 CID 的视频详情，并且只把本次新增成员加入下载队列。

### 歌曲

```
GET  /api/songs?status=ready        → [Song…]（曲库列表）
GET  /api/songs/{id}                → Song（含歌词）
PATCH /api/songs/{id}               // 修改元数据，音频文件标签同步重写
  { "title": "…", "artist": "…", "album": "…" }
POST /api/songs/{id}/cover/refresh  → 刷新封面和内嵌封面，返回 Song；失败 502，下载/编辑中 409
POST /api/artists/sync             → {"artists": 12}，生成客户端歌手分类歌单
DELETE /api/songs/{id}              → {"deleted": true}   // 删除音频/.lrc/封面/记录
PUT    /api/songs/{id}/lyrics       // multipart/form-data，file=.lrc 或 .txt，最大 1 MB
DELETE /api/songs/{id}/lyrics       // 删除数据库、.lrc 侧车和 M4A 内嵌歌词
```

### 歌单

```
GET    /api/playlists                         → [{id,name,song_count,created_at}]
POST   /api/playlists  {"name":"通勤"}       → 创建歌单
PATCH  /api/playlists/{id} {"name":"夜间"}  → 重命名
DELETE /api/playlists/{id}                    → 只删除歌单，不删除歌曲文件
GET    /api/playlists/{id}/songs              → 按加入顺序返回 [Song…]
POST   /api/playlists/{id}/songs/{song_id}    → 加入歌曲（重复请求幂等）
DELETE /api/playlists/{id}/songs/{song_id}    → 从歌单移除
```

歌单只保存歌曲引用与顺序，不复制音频、封面或歌词。删除歌曲时，其所有歌单关系由数据库级联清理。

### 媒体

```
GET /api/songs/{id}/cover            // 封面（服务端本地 JPEG 缓存）
GET /api/stream/{id}                 // 音频流（audio/mp4，支持 Range 拖动进度）
```

### 备份导出

```
GET /api/export.csv                  // 全曲库 CSV（UTF-8 BOM，Excel 友好）
```

CSV 列：`id,歌曲名,歌手,专辑,bvid,B站视频链接,分P,时长(秒),状态,歌词来源`。

## 典型流程

```bash
BASE=http://127.0.0.1:8080
TOKEN=xxx

# 1. 解析（私密收藏夹带 cookie）
curl -X POST $BASE/api/jobs -H "X-Api-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"url":"https://space.bilibili.com/12345678/favlist?fid=1234567890","cookie":"SESSDATA=…"}'

# 2. 修改某首元数据（可选）
curl -X PATCH $BASE/api/songs/149 -H "X-Api-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"artist":"呜米"}'

# 3. 开始下载
curl -X POST $BASE/api/jobs/<job_id>/start -H "X-Api-Token: $TOKEN" \
  -H "Content-Type: application/json" -d '{"song_ids":[149]}'

# 4. 轮询进度
curl $BASE/api/jobs/<job_id> -H "X-Api-Token: $TOKEN"

# 5. 播放
curl -r 0-100000 "$BASE/api/stream/149?token=$TOKEN" -o /dev/null
```

音频端点 `GET/HEAD /api/stream/{sid}` 支持 HTTP Range（206 / 416），使用 `Content-Disposition: inline` 和私有缓存重新验证；会话或 API Token 鉴权同其他受保护端点。

CSV 导出会在可能被 Excel 等解释为公式的文本前加单引号（`=`, `+`, `-`, `@`，包括前导空白），曲库原始文本不变。不要把真实 API Token 或客户端密码写入文档；从部署环境或密码管理器读取。

## 2026-09-12：识别依据与重新识别

`Song` 新增 `description: string|null`（视频简介）、`metadata: object|null`（识别依据）。旧库自动补列，历史手工信息保留。

```json
{
  "title": "烟花",
  "artist": "演唱者",
  "title_source": "title_book",
  "artist_source": "description_credit",
  "title_confidence": 0.98,
  "artist_confidence": 0.96,
  "needs_review": false,
  "reasons": [],
  "parser_version": 3
}
```

分数仅表示启发式证据强度，不是经过校准的正确率。没有演唱证据时返回“未知歌手”，上传者单独保留在 `uploader`。已有库中手工确认/强署名的歌手，以及同批明确署名，用于精确匹配和统一人名；不会因同一个上传账号或相似标题就合并歌手。

导入时，`artist_confidence < 0.8` 的候选仍保存在 `metadata.artist`，实际 `Song.artist` 使用“未知歌手”，避免订阅自动生成错误歌手分组。重新识别接口返回候选供人工确认。没有独立歌名依据的中文长句正文会标记待核对。

```text
POST /api/songs/{id}/analyze
POST /api/songs/{id}/analyze?refresh=true
```

返回上述建议及 `warnings: string[]`，不修改歌曲或音频。默认使用缓存证据；`refresh=true` 尝试刷新公开视频标签与简介，失败时使用缓存并返回提示。仍需普通 API 鉴权。用户采纳后通过原有 PATCH 保存。

PATCH 歌名/歌手后，对应来源记为 `manual`，以后日更保留。歌名/歌手不可为空或超过 300 字；专辑可以清空。每个P已经独立建歌，PATCH 改成另一个 `cid` 返回 400；分P序号、标题和实际时长不允许客户端覆盖。下载/编辑进行中再次修改返回 409；音频标签保存失败返回 503，数据库不先行更新。

收藏夹分页中途失败会拒绝不完整快照并保留原成员关系。日更只下载缺失音频，包含上次下载失败的成员；元数据请求使用全局线程池。取消预览不会删除其他任务或已保存收藏夹引用的歌曲。

## 2026-10-10：逐 P 下载与客户端歌手分类

歌曲以 `(bvid, part_index)` 联合唯一，前端必须用 `song_ids` 提交选择，不能用 BV 区分同视频各P。`bvids` 仅保留脚本兼容，选择该BV在任务中的全部P；同时提供 `song_ids` 时以歌曲ID为准。显式选择会持久化任务中多P条目的 `download_requested`；服务内部的自动排队不会更改待选择条目。

收藏夹分P接口中，`bvids` 是本次完整核对的视频范围，`song_ids` 是该范围内选中的歌曲；允许空 `song_ids` 保存「全部不下载」，范围外歌曲ID返回400。只保存选择，不删除未选歌曲或已有音频。`multipart_policy` 仅决定尚未明确选择的P，已经保存的手工选择优先。

旧库首次启动在表重建前生成 `musicplayer.pre-multipart.bak`，保留原歌曲ID、音频路径、创建时间、收藏夹/歌单/任务关系和AUTOINCREMENT上限；缓存多P记录补齐缺少的P并等待用户重新选择。仅有首P且未取得完整详情的旧记录，会在收藏夹接口提示更多P时请求详情补齐。备份可在停服务后恢复，备份之后新增的记录不会随旧库恢复保留。

歌手分类保存在音乐目录的 `MusicPlayer-Artists/*.nsp`，按当前DB中的演唱者和准确音频路径生成，Navidrome扫描导入后可由Subsonic客户端打开。与已有 `MusicPlayer-Playlists` 只读挂载分开，不修改专辑分组或复制音频。旧空分类写入空规则，防止客户端保留过期曲目。

## 管理员账户和可选 2FA

`GET /api/auth/status` 额外返回 `two_factor_enabled` 和部署配置中的 `navidrome_url`；登录请求 `POST /api/auth/login` 可携带 `totp_code`（6 位字符串），仅在开启 2FA 时必填。图片验证码仍必填，失败共享 IP 限速。

以下接口要求有效管理员浏览器 Cookie，API_TOKEN 不可代替；修改请求都须携带 `current_password`，开启 2FA 后还需当前未使用的 `code`：

| 请求 | 用途 / 附加字段 |
| --- | --- |
| `GET /api/auth/settings` | 返回当前 `username`、`two_factor_enabled`，不含摘要或密钥 |
| `PUT /api/auth/settings` | 修改 `username` / `new_password`，新密码留空保留，至少 12 字 |
| `POST /api/auth/2fa/setup` | 创建 10 分钟有效的待绑定密钥；返回 `secret`、`uri` 和本地生成的 `qr_image`，验证前不启用 |
| `POST /api/auth/2fa/enable` | 用 `code` 验证待绑定密钥，再启用 2FA |
| `POST /api/auth/2fa/disable` | 用当前密码和动态码关闭 2FA |

修改返回 `username`、`two_factor_enabled`。账户保存或开启 / 关闭 2FA 会轮换会话版本，并刷新当前浏览器 Cookie，其他浏览器会话失效。动态码使用 RFC 6238 / SHA1 / 30 秒 / 6 位，允许相邻时间窗并防重放，验证和消耗在同一 SQLite 事务内。账户设置写入新增 `admin_account` 单行表，首次修改前使用安装环境的初始管理员。找回只支持服务器本地命令，见账户安全教程。

### 封面下载与历史缓存

音频下载取得的封面缺失时，使用收藏夹已保存的图片地址，无需先请求受限的详情接口。重新下载封面也优先使用该地址。历史远程封面的 GET 由服务器通过 HTTPS 和 B 站 Referer 下载，按地址摘要缓存 JPEG 后返回，不再把浏览器重定向到 HTTP 图片；缓存生成不修改歌曲资料。只允许 hdslb.com 子域、禁止跟随重定向、源图读取限制 10 MiB，本地 JPEG 最大 320 像素、每张不超过 1 MiB，最多四个图片请求并发；取图失败返回 503 / Retry-After，不阻断音频。

### 重新匹配歌词

管理员 `POST /api/songs/{id}/lyrics/refresh` 返回 `{matched, message, song}`。按已保存的歌名、歌手和实际音轨时长重新查询；成功同步数据库、音频内嵌歌词和侧车文件，音频版本随之改变。未找到时保留原歌词，手动上传来源永不自动覆盖。编辑歌曲信息内提供按钮；先保存名字修改再匹配。

### 歌词失败通知与五分钟重试

- `GET /api/lyrics-failures`：管理权限，返回与下载失败通知相同的歌曲/错误/状态/尝试次数和 UTC 时间字段。
- `POST /api/songs/{id}/lyrics/retry`：加入单独的歌词匹配队列，返回 `{started,queued,message}`；同歌曲下载或编辑占用时不重复提交。
- `POST /api/songs/{id}/lyrics/failure/dismiss`：关闭并跳过通知，取消该歌曲的歌词自动重试。手动重新匹配可再次激活。

失败结果持久化到 lyrics_failures，每次失败完成后五分钟重试，服务重启可恢复。歌词队列不会重新下载音频，使用现有歌曲编辑占用，成功更新歌词与侧车并保留通知至手动关闭。旧曲库已下载且自动歌词开启但为空的条目也会建立通知。管理员界面提示旁提供重试、上传 .lrc/.txt 和叉号；手动上传的歌词不被自动匹配覆盖。未命中时保留既有歌词，分P序号仅在检索时尝试去除；缓存负结果五分钟、正结果一天，重试可强制刷新。网易旧搜索在海外返回非对象时使用官方 cloudsearch 备用搜索，不能保证所有歌曲都有可用歌词。
