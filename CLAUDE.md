# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 現状

**実装前の段階**。リポジトリには設計計画書と JPO API のリファレンス資産しか入っていない。ソースコード・ビルド設定・テスト・Dockerfile はまだ何も書かれていない。`PLAN.md` が唯一の真実の源 (source of truth) — 何かを始める前に必ず読むこと。

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

## デプロイ先

LAN ホスト `<DEPLOY_HOST>`、SSH ユーザー `<SSH_USER>`、Docker + `docker compose`。Compose のポートバインドは `<DEPLOY_HOST>:8765:8765` とする — `0.0.0.0` は禁止 — これで自宅 LAN からしか到達できない。MCP トランスポートは HTTP+SSE（Claude Desktop の "Remote MCP" 設定）を使う。stdio はホスト跨ぎでは使えないので不採用。

JPO 認証情報はデプロイホストの `~/ip-mcp/.env`（`chmod 600`）にのみ置く。Git にコミットしない。`.gitignore` で `.env` / `cache/` / `logs/` を除外する。

## ビルド / テスト / 実行

まだ実行できるものは何もない — ソースツリー、`pyproject.toml`、`Dockerfile`、`docker-compose.yml` はまだ作っていない。`PLAN.md` §6 / §8 に従って作成してから、このセクションにコマンドを追記すること。完成後の正規コマンドは `docker compose up -d --build`（デプロイ）と `uv run pytest`（テスト）になる予定だが、それまで「動かし方」を答えるのは時期尚早。

## このフォルダで動いているツール群

- `.vscode/tasks.json` がフォルダオープン時に `throughline monitor` 端末を自動起動する（トークン使用量表示）。
- `.claude/settings.json` で `claude-spotter` のフック (SessionStart / UserPromptSubmit / PreToolUse / Stop / SessionEnd) を有効化。セッション中にツール選択を監査する。Spotter のフィードバックは `Stop hook` メッセージとして届く — ノイズではなくピアレビュアーとして扱うこと。
- `WebFetch` は `www.jpo.go.jp` と `ip-data.jpo.go.jp` に対して事前許可済み。JPO ドキュメントはプロンプトなしで取得できる。
