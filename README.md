# Trip Agent

Slack ↔ Dropbox 브리지 봇. `#출장일지` 채널의 스레드 댓글과 첨부파일을 Dropbox의 일자별 폴더로 자동 정리하고, 모바일에서 대용량 영상을 Dropbox 네이티브 앱으로 바로 업로드할 수 있는 링크를 제공한다. 업로드 완료 시 해당 쓰레드에 알림까지 돌려준다.

## 동작 개요

1. 출장 시작 전, `#출장일지` 채널에 parent 메시지 형식으로 쓰레드를 연다: `YYMMDD-DD, {나라}, {행사명}`
2. 출장 중 팀원이 스레드에 일지·사진·파일을 올린다.
3. `!아카이브` — 쓰레드 replies가 `messages.md` + 첨부파일 형태로 Day N 폴더에 동기화
4. `!대용량` — 오늘 Day N 폴더의 Dropbox 앱 링크 발급 (모바일 1GB+ 영상용)
5. Dropbox watcher가 60초마다 새 파일 감지 → 해당 쓰레드에 업로더·파일명 알림

## 출장 제목 형식

```
YYMMDD-DD, {나라}, {행사명}
260501-07, 미국, DDW 2026
260512-17, 이탈리아, ESGE 2026
260409-12, 중국, CMEF 2026, CACA 2026   # 한 출장이 다수 행사면 콤마로 이어붙임
```

Day N 폴더 예시: `260501-1st Day`, `260502-2nd Day` ...

타임존 override는 [`tasks/trip_timezone_overrides.json`](tasks/trip_timezone_overrides.json)에 full title 기준으로 지정.

## Slack 명령어

| 명령어 | 위치 | 동작 |
|---|---|---|
| `!아카이브` | 출장 쓰레드 댓글 | 쓰레드 전체 replies + 첨부파일을 Dropbox 일자별 폴더에 동기화 |
| `!대용량` | 출장 쓰레드 댓글 | 오늘 Day N 폴더로 바로 여는 Dropbox 앱 링크를 쓰레드에 응답 |

둘 다 채널 root에서 쓰면 무시됨 — 반드시 출장 쓰레드 안에서 써야 함.

## 컴포넌트

| 경로 | 역할 |
|---|---|
| [`tasks/trip_listener.py`](tasks/trip_listener.py) | Socket Mode 리스너 (`!아카이브`·`!대용량` 처리) + watcher 스레드 실행 |
| [`tasks/daily_trip_archive.py`](tasks/daily_trip_archive.py) | 쓰레드 → Dropbox 동기화 오케스트레이터 (`messages.md` 병합 포함) |
| [`tasks/dropbox_upload_watcher.py`](tasks/dropbox_upload_watcher.py) | `files/list_folder/continue` 커서로 delta 폴링 → 쓰레드 알림 |
| [`tasks/trip_parser.py`](tasks/trip_parser.py) | 출장 제목 파싱 (날짜·나라·행사) |
| [`tasks/trip_timezones.py`](tasks/trip_timezones.py) | 나라별 IANA TZ 매핑 + override |
| [`tasks/setup_dropbox_refresh.py`](tasks/setup_dropbox_refresh.py) | 1회성 Dropbox refresh 토큰 발급 도우미 |
| [`connectors/dropbox/`](connectors/dropbox/) | Dropbox REST 클라이언트 (refresh flow + 팀 스페이스 헤더) |
| [`connectors/slack/`](connectors/slack/) | Slack Web API 클라이언트 (form-urlencoded POST) |

## 환경 변수

`.env` 파일을 프로젝트 루트에 생성. [`.env.example`](.env.example) 참고. 주요 값:

**Dropbox** (앱은 Team member 스코프)
- `DROPBOX_APP_KEY` / `DROPBOX_APP_SECRET` — Dropbox App Console에서 발급
- `DROPBOX_REFRESH_TOKEN` — `python tasks/setup_dropbox_refresh.py`로 발급
- `DROPBOX_TEAM_MEMBER_ID` — `dbmid:...` (팀 스코프 토큰 사용 시 필수)

**Slack** (Trip Agent 봇)
- `TRIP_BOT_TOKEN` — `xoxb-...` Bot User OAuth Token
- `TRIP_USER_TOKEN` — `xoxp-...` User Token (트리거 메시지 삭제용)
- `TRIP_APP_TOKEN` — `xapp-...` App-Level Token (Socket Mode 연결)
- `TRIP_CHANNEL_ID` — `#출장일지` 채널 ID

## Slack 봇 스코프

Socket Mode 활성화 + 다음 스코프 필요:
- Bot: `chat:write`, `chat:write.public`, `channels:history`, `channels:read`, `files:read`, `im:history`
- User: `chat:write`, `channels:history` (트리거 삭제용)

이벤트 구독: `message.channels`

## 로컬 실행

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python tasks/trip_listener.py
```

`Ctrl+C`로 종료. Windows 콘솔 UTF-8은 `sys.stdout.reconfigure`로 내장 처리됨.

## 배포 (GCP)

상시 구동은 GCP Compute Engine **e2-micro (무료티어)** + systemd 서비스.

- 현재 배포: `tripagent-svc` 프로젝트 / `us-west1-a` / Debian 12
- 프로세스: `Restart=always`, 재부팅 시 자동 시작
- 로그: `journalctl -u trip-listener -f`

상세 절차는 [`deploy/README.md`](deploy/README.md) 참조.

## 상태 파일 (런타임 생성)

`state/` 디렉토리는 gitignore. 런타임에 만들어짐:
- `trip_archive_state.json` — 쓰레드별 `last_archived_ts`
- `dropbox_watcher_state.json` — Dropbox 변경 감지 커서

## 요구사항

- Python 3.11+
- Dropbox 앱 (Team member 스코프, `files.content.write` 등 전체 권한)
- Slack 봇 (Socket Mode, 위 스코프)
- 상시 구동 환경 (GCP VM 또는 데스크톱/NAS)

## 제약·주의

- **로컬 실행 시 PC 꺼지면 중단** — 상시 서비스용은 GCP 배포 권장.
- `!아카이브`로 생성된 파일(`messages.md`, `{file_id[:8]}_{원본명}`)은 watcher가 자동 무시 (피드백 루프 방지).
- Dropbox 팀 스코프 토큰은 모든 요청에 `Dropbox-API-Select-User` 헤더 필수 — [`connectors/dropbox/client.py`](connectors/dropbox/client.py)가 자동 주입.
- 파일명·헤더의 non-ASCII 문자는 Dropbox-API-Arg용으로 `\uXXXX` 이스케이프 — [`connectors/dropbox/upload.py`](connectors/dropbox/upload.py) 처리.
