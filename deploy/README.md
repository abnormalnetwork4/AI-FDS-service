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
- 마지막에 **화면 접속 아이디/비밀번호**를 정하라고 물어봅니다. 팀원과 공유할 값입니다.
- 5~10분 걸립니다.

## 4. AI 키 넣기

```bash
nano backend/.env
```

아래처럼 채우고 `Ctrl+O` → Enter → `Ctrl+X`로 저장:

```
ANTHROPIC_API_KEY=여기에_MonoGPT_키
ANTHROPIC_BASE_URL=https://monogpt.kr/api/monorouter/v1/anthropic/v1
CLAUDE_MODEL=claude-sonnet-4-6
```

```bash
sudo systemctl restart fds-chat
```

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
| 채팅이 "AI 응답 실패 (HTTP …)" | `backend/.env` 키·주소·모델 확인 후 `sudo systemctl restart fds-chat` |
| 접속 비밀번호 바꾸기 | `sudo htpasswd /etc/nginx/.htpasswd <아이디>` |

## 주의

- 운영 서버에서는 화면의 **기록 비우기가 꺼져 있습니다**(`FDS_ALLOW_CLEAR=0`).
- 로그인 기능이 없어서 채팅 사용자 ID는 각자 정하는 시연용입니다. 접속 자체는 nginx 비밀번호로만 막습니다.
- HTTP라서 비밀번호가 암호화되지 않습니다. 실제 운영 전에는 도메인 + HTTPS(Let's Encrypt)를 붙이세요.
- 데모가 끝나면 EC2 인스턴스를 **중지**해야 요금이 안 나갑니다(탄력적 IP는 연결 안 된 상태로 두면 요금이 붙음).
