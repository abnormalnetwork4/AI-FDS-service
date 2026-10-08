# AWS(EC2)에 올리기

서버 한 대에 세 가지가 올라갑니다.

```
브라우저 ──(80, 아이디/비밀번호)──> nginx ─┬─ /            대시보드·채팅 화면 (frontend/dist)
                                          ├─ /api/       FDS 분석 서버 (127.0.0.1:8000)
                                          └─ /chat-api/  사내 AI 채팅 서버 (127.0.0.1:8100) ──> MonoGPT/Claude
```

파이썬 서버 두 개는 서버 안에서만 열리고, 밖에서는 nginx(80번)로만 들어옵니다.

## 1. EC2 서버 만들기 (AWS 콘솔, 처음 한 번)

1. AWS 콘솔 → **EC2** → **인스턴스 시작**
2. 이름: `ai-fds`
3. OS(AMI): **Ubuntu Server 24.04 LTS**
4. 인스턴스 유형: **t3.small** 이상 (t3.medium 권장, 화면 빌드할 때 메모리가 필요함)
5. 키 페어: **새 키 페어 생성** → 이름 `ai-fds` → `.pem` 파일이 내려받아짐. **잃어버리면 접속 못 하니 잘 보관**
6. 네트워크 설정 → 보안 그룹:
   - SSH(22): 소스 **내 IP**
   - HTTP(80): 소스 **위치 무관(0.0.0.0/0)** — 접속은 nginx 비밀번호로 막습니다
7. 스토리지: 20GB
8. **인스턴스 시작** → 인스턴스 목록에서 **퍼블릭 IPv4 주소**를 확인 (예: `3.35.12.34`)

> IP는 서버를 껐다 켜면 바뀝니다. 고정하려면 EC2 → **탄력적 IP** 할당 후 인스턴스에 연결하세요.

## 2. 서버 접속 (내 PC PowerShell)

```powershell
cd $HOME\Downloads
ssh -i .\ai-fds.pem ubuntu@<퍼블릭IP>
```

처음에 `yes` 입력. `.pem` 권한 오류가 나면:

```powershell
icacls .\ai-fds.pem /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

## 3. 설치 (서버 안에서)

```bash
git clone https://github.com/abnormalnetwork4/AI-FDS-service.git
cd AI-FDS-service
sudo bash deploy/install.sh
```

- 저장소가 비공개면 `git clone` 때 GitHub 아이디와 **Personal Access Token**(비밀번호 자리)을 넣습니다.
- 중간에 **AI 키**를 물어봅니다(화면에 안 보임). MonoGPT 키면 주소·모델이 자동으로 MonoGPT로 설정됩니다.
- 마지막에 **화면 접속 아이디/비밀번호**를 정하라고 물어봅니다(두 번이 다르면 다시 물어봄). 팀원과 공유할 값입니다.
- 끝에 FDS·채팅 서버 상태와 AI 설정을 확인해서 보여 줍니다.
- 5~10분 걸립니다.

## 4. 설정 바꾸기 (`backend/.env`)

설치 때 키를 건너뛰었거나 설정을 바꾸려면:

```bash
nano backend/.env
```

| 항목 | 의미 |
|---|---|
| `ANTHROPIC_API_KEY` | MonoGPT(또는 Anthropic) 키 |
| `ANTHROPIC_BASE_URL` | MonoGPT: `https://monogpt.kr/api/monorouter/v1/anthropic/v1` |
| `CLAUDE_MODEL` | MonoGPT: `claude-sonnet-4-6` |
| `FDS_ALLOW_CLEAR` | 화면의 기록 비우기 `1` 켜기 / `0` 끄기 |

저장(`Ctrl+O` → Enter → `Ctrl+X`) 후 재시작:

```bash
sudo systemctl restart fds fds-chat
```

채팅 설정 확인: `http://<퍼블릭IP>/chat-api/health` — `warning`이 비어 있어야 합니다.

## 5. 접속

- 대시보드: `http://<퍼블릭IP>/`
- 사내 AI 채팅: `http://<퍼블릭IP>/chat.html`

## 코드 업데이트할 때

```bash
cd ~/AI-FDS-service
git pull
sudo bash deploy/install.sh
```

`.env`와 접속 비밀번호, DB(`backend/data`)는 그대로 유지됩니다.

## 문제가 생기면

| 증상 | 확인 |
|---|---|
| 페이지가 안 열림 | 보안 그룹에 HTTP(80) 열었는지, `sudo systemctl status nginx` |
| 대시보드는 뜨는데 데이터가 안 옴 | `sudo systemctl status fds --no-pager`, `journalctl -u fds -n 50` |
| 채팅이 "서버에 연결하지 못했어요" | `sudo systemctl status fds-chat --no-pager`, `journalctl -u fds-chat -n 50` |
| 채팅이 "AI 응답 실패 (HTTP 401)" | 키 오류. 메시지에 "MonoGPT 주소" 안내가 있으면 `ANTHROPIC_BASE_URL`이 빠진 것. `sudo bash deploy/install.sh`를 다시 돌리면 자동으로 채워짐 |
| 채팅이 "AI 응답 실패 (HTTP 403)" | 크레딧·키 권한 확인 (monogpt.kr) |
| 재시작이 오래 걸림 | 최대 15초. 그 이상이면 `sudo systemctl status fds --no-pager` |
| 접속 비밀번호 바꾸기 | `sudo htpasswd /etc/nginx/.htpasswd <아이디>` |

## 주의

- 기록 비우기는 기본으로 **켜져 있습니다**(시연용, 지우기 전에 DB 백업을 자동으로 만듦). 실제 운영에서는 `.env`에서 `FDS_ALLOW_CLEAR=0`으로 끄세요.
- 로그인 기능이 없어서 채팅 사용자 ID는 각자 정하는 시연용입니다. 접속 자체는 nginx 비밀번호로만 막습니다.
- HTTP라서 비밀번호가 암호화되지 않습니다. 실제 운영 전에는 도메인 + HTTPS(Let's Encrypt)를 붙이세요.
- 데모가 끝나면 EC2 인스턴스를 **중지**해야 요금이 안 나갑니다(탄력적 IP는 연결 안 된 상태로 두면 요금이 붙음).
