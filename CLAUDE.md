# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 現状

**Phase 1A / 1B / 1.x 完了済（2026-05-01 時点）**。リポジトリには動作する実装が入っている:

- `src/ip_mcp/tools_official/` — 12 ツール (番号変換 / 経過情報 / 登録 / 引用 / 関連出願 / 出願人 / 書類 / J-PlatPat URL / OPD / 全件取得)
- `src/ip_mcp/tools_external/` — 1 ツール (Google Patents キーワード検索、独立実装)
- `src/ip_mcp/auth/` — OAuth 2.1 サーバー (DCR + PKCE + マスターパスワード認可、`SqliteOAuthProvider` で SQLite 永続化)
- `Dockerfile` / `docker-compose.yml` — `./cache`, `./logs`, `./data` の 3 ボリューム
- `tests/` — 45 テスト、全緑

公開構成: `ipmcp.<domain>.dynv6.net` (Caddy + Let's Encrypt) → `<DEPLOY_HOST>:8765` → Docker コンテナ。同 LAN 内からは Windows hosts に `<DEPLOY_HOST> ipmcp.<domain>.dynv6.net` を入れることでヘアピン NAT を回避。

**Phase 1.5 完了**: OAuth 永続化 / ヘアピン NAT 回避 / アクセスログ JSONL (`logs/access.jsonl`) + 集計スクリプト (`scripts/summarize_logs.py`) / マスターパスワード rotate 手順 ([OPERATIONS.md](OPERATIONS.md))。
**Phase 2 (未着手)**: 拒絶理由通知書 PDF 構造化、AI レビューパイプライン、EPO OPS / WIPO PATENTSCOPE 補完。

`PLAN.md` は設計計画書として継続維持中（§11 段階表に進捗、§12 リスク表で完了済リスクを取り消し済）。

GitHub: <https://github.com/kitepon-rgb/IP-MCP> （public、これが正規リモート）

## このプロジェクトが目指すもの

特許庁の公式「特許情報取得API」(基底 `https://ip-data.jpo.go.jp`) を包む **Python 製 MCP (Model Context Protocol) サーバー** を作り、LLM クライアントから呼べるようにする。`<DEPLOY_HOST>`（自宅 LAN, SSH ユーザー `<SSH_USER>`）の Docker 上で常駐させ、Claude Desktop / Claude Code から HTTP+SSE で利用する。

## 譲れないアーキテクチャ規則

ユーザーから明示的に指示された方針。これを破ったら退行 (regression)。逸脱の前に必ず質問すること。

1. **データソース間の暗黙のフォールバック禁止**。公式 JPO API の呼び出しが失敗 (not_found / レート上限 / 一時障害) しても、Google Patents 等の外部ソースに自動で切り替えてはならない。失敗は失敗としてそのまま返し、別ツールを呼ぶかどうかは LLM 側に判断させる。
2. **ツールは完全に分離された 2 カテゴリに分ける**:
   - `tools_official/` — 公式 JPO API を叩く。ツール名は `jpo_*` で始める。レスポンスは `{"source": "jpo_official", ...}`。
   - `tools_external/` — 非公式ソース (キーワード検索用の Google Patents XHR 等) を叩く。ツール名は `external_*` で始める。レスポンスは `{"source": "google_patents_unofficial", ...}`。
   - **`tools_official/` から `tools_external/` への import は禁止（逆も同様）**。これは完了基準として強制する。
3. **同一ツール内で許される再試行は次の 2 つだけ**: 401/期限切れ時のトークン再取得、JPO の `statusCode 303`（一時的な高負荷）に対する指数バックオフ。どちらも同一データソース内に閉じる。
4. すべてのツール description は冒頭に `Source:` 行を必ず置き、データの出所が公式か非公式かを LLM が判別できるようにする。レスポンスペイロードにもトップレベルに `source` フィールドを必ず含める。

## なぜキーワード検索を別ツールに分離したのか（パラメータではなく）

公式 JPO API は **全 42 エンドポイントすべてが番号ルックアップ型**（入力は出願/公開/登録番号、申請人コード、または**完全一致**の申請人氏名）。キーワード検索・IPC 検索・F ターム検索・日付レンジ・部分一致検索は仕様書のどこにも存在しない。これは生の OpenAPI 仕様 (`api_reference.json` がこのフォルダにある、上流は <https://ip-data.jpo.go.jp/api_guide/api_reference.js>) を grep して実証済み。今後「検索ツールに IPC フィルタを追加して」といった依頼が来た場合、それを実装できるのは外部の Google Patents ツール側だけで、公式ツール側には絶対に追加できない。

## 参考資産

| ファイル | 用途 |
|------|---------|
| `PLAN.md` | 設計計画書の全文。§4.1 / §4.2 に実装すべき MCP ツール全リスト、§7 に旧プロジェクトからの移植マップ。 |

### Git 管理外の参考資料 (`.gitignore` 済)

JPO 公式仕様書のローカルコピーは Public リポジトリに含めていない（著作権・利用規約未確認のため）。必要時は次から取得:

- OpenAPI 仕様 (raw JS): <https://ip-data.jpo.go.jp/api_guide/api_reference.js>
- Swagger UI: <https://ip-data.jpo.go.jp/api_guide/api_reference.html>
- ローカルにキャッシュする場合はファイル名 `api_reference.js` / `api_reference.json` / `jpo_reference.html` で `.gitignore` 済み。

## 外部依存: 旧 Flask プロジェクト

JPO API クライアントの動く実装が **`../IP`** に既に存在する。これはサブモジュールでも依存でもなく、**コードのドナー**として使う。`tools_official/` を実装するときは、ゼロから書かずにそこから移植すること:

| 移植元 (`Program\IP\` 内) | 移植先 (本リポジトリ) |
|---|---|
| `jpo_patent_claims_fetch.py` の `fetch_access_token`, `build_headers`, `fetch_json` (~L391–477) | `src/ip_mcp/jpo/client.py` |
| `jpo_patent_claims_fetch.py` の `normalize_*_number` (L93/102/111) | `src/ip_mcp/jpo/normalize.py` |
| `jpo_patent_claims_fetch.py` の `search_google_patents` + `SearchUnavailableError` (L2711) | `src/ip_mcp/tools_external/google_patents_search.py` (独立、ここに JPO クライアントを import しない) |
| `patent_api/_helpers.py` の `parse_identifier`, `build_patent_status` | `src/ip_mcp/jpo/normalize.py`, `tools_official/registration.py` |

`Program\IP\` から **持ち込まないもの**: Flask Blueprint、HTML テンプレート、OCR/PDF パイプライン (Phase 2 で別途検討)、AI レビューパイプライン。

## JPO API の罠（実装時に必ず引っかかる）

- HTTP は常に 200。本当の成否は JSON ボディ内の `result.statusCode` を見る。主なコード: `100`=成功 / `107`=該当なし / `203`=日次上限 / `204/208`=パラメータエラー / `210`=トークン無効 / `303`=一時障害（再試行可）/ `999`=想定外。
- 認証は OAuth2 **Resource Owner Password Grant**、`POST /auth/token`。アクセストークン TTL は 1 時間、リフレッシュトークン TTL は 8 時間。同一 ID で複数クライアントから同時アクセス可（公式 OK）。
- クライアント側で守るレート制限: `/api/patent/*` は **10 req/分**、`/opdapi/*` は **5 req/分**。日次上限はエンドポイントごとに 30〜800/日。`result.remainAccessCount` が全レスポンスに入っているので必ずログに残す。
- 入力番号は **半角数字 10 桁**。元号年表記（令和N年特願…）は西暦変換してから渡す。
- `applicant_attorney/{氏名}` は **完全一致のみ**（スペース有無・全角半角・大文字小文字すべて影響）。LLM が曖昧検索を試さないよう、ツール description にこの制約を明記すること。
- `app_doc_cont_*` 系（拒絶理由・特許査定・意見書/補正書）は **JSON ではなく ZIP バイナリ直返し**のケースがある（小サイズ書類は inline、大サイズだけ JSON envelope + signed URL）。`response.json()` を無条件に呼ぶと UnicodeDecodeError で死ぬ。`JpoClient.get_raw()` を使い、`is_binary` で分岐すること。検出は Content-Type と PK\\x03\\x04 マジック両方で行う。

## デプロイ先

LAN ホスト `<DEPLOY_HOST>`、SSH ユーザー `<SSH_USER>`、Docker + `docker compose`。Compose のポートバインドは `<DEPLOY_HOST>:8765:8765` とする — `0.0.0.0` は禁止 — これで自宅 LAN からしか到達できない。MCP トランスポートは HTTP+SSE（Claude Desktop の "Remote MCP" 設定）を使う。stdio はホスト跨ぎでは使えないので不採用。

JPO 認証情報はデプロイホストの `~/ip-mcp/.env`（`chmod 600`）にのみ置く。Git にコミットしない。`.gitignore` で `.env` / `cache/` / `logs/` を除外する。

## ビルド / テスト / 実行

ローカル開発:
- `uv run pytest` — 全 45 テスト (オフライン、JPO 認証情報不要)
- `uv run ruff check src/ tests/` — 静的チェック
- `uv run python -m ip_mcp.server` — ローカル単独起動 (要 `.env`)

Docker:
- `docker compose up -d --build` — ビルド + 起動 (デフォルトは `127.0.0.1:8765` バインド)
- `docker compose logs -f ip-mcp` — ログ追跡
- LAN 公開する場合は `docker-compose.override.yml` を別途作成（`.gitignore` 済、サンプルあり）

デプロイ更新 (`<DEPLOY_HOST>`):
```bash
ssh <SSH_USER>@<DEPLOY_HOST> "cd ~/ip-mcp && git pull && docker compose up -d --build"
```

OAuth 永続化のため `./data:/app/data` ボリュームがマウントされる。コンテナ再起動・再ビルド後もクライアント登録・トークンは生き残る。マスターパスワード変更時のみ iPhone 側の再認可が必要。

## このフォルダで動いているツール群

- `.vscode/tasks.json` がフォルダオープン時に `throughline monitor` 端末を自動起動する（トークン使用量表示）。
- `.claude/settings.json` で `claude-spotter` のフック (SessionStart / UserPromptSubmit / PreToolUse / Stop / SessionEnd) を有効化。セッション中にツール選択を監査する。Spotter のフィードバックは `Stop hook` メッセージとして届く — ノイズではなくピアレビュアーとして扱うこと。
- `WebFetch` は `www.jpo.go.jp` と `ip-data.jpo.go.jp` に対して事前許可済み。JPO ドキュメントはプロンプトなしで取得できる。
