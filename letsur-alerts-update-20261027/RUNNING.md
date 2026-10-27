# 내가 할 일과 설치 방법

## 지금 노트북에서 테스트할 때

현재는 노트북의 프로그램이 Slack으로 알림을 보냅니다. Slack에 봇만 설치했다고 자동으로 실행되는 구조는 아닙니다.

1. **전원을 연결하고 인터넷과 Codex를 켜 둡니다.** Codex를 화면에 띄워 둘 필요는 없지만 완전히 종료하면 안 됩니다.
2. **절전·최대 절전·종료 상태로 들어가지 않게 합니다.** 화면만 꺼지는 것은 괜찮습니다. 덮개를 닫으면 절전되는 설정이라면 열어 둡니다.
3. Slack에 온 사건을 확인하고, 재알림을 멈추려면 카드의 **‘확인함’**을 누릅니다. Slack 화면은 계속 열어 두지 않아도 됩니다.

원 개발 PC는 설치·인증·10분 간격 예약이 끝나 있어 매번 명령을 실행할 필요가 없습니다. PC를 끄면 발송과 확인 버튼 처리가 멈춥니다. 다시 켜고 Codex를 실행하면 다음 예약에서 재연결을 시도합니다. 확인 기록은 남습니다.

이 상태로 개인 노트북을 계속 켜 두는 것은 **테스트용**입니다. 렛서에 넘겨 상시 운영할 때는 계속 실행되는 PC나 서버로 옮겨야 합니다. 현재 실행기는 Windows용이며 Linux 서버에서는 수정·검증이 필요합니다.

## 다른 PC에 설치할 때만

Windows, Python 3.12 이상, GitHub 결과물 접근 권한이 필요합니다.

```powershell
python -m pip install --target .runtime -r requirements.txt
python -m unittest discover -v
python offline_demo.py
Copy-Item .env.example .env
```

[Slack 연결 안내](SLACK_SETUP.md)에 따라 `.env`와 `operations.json`을 채웁니다. `.env`는 토큰(앱 인증키)을 넣는 개인 설정 파일이므로 GitHub에 올리지 않습니다.

GitHub 인증은 Git Credential Manager 또는 환경변수 `GH_TOKEN`/`GITHUB_TOKEN`을 사용합니다. 결과물을 읽을 권한이 필요하며, GitHub 토큰은 Slack용 `.env`에 추가하지 않습니다.

```powershell
python operations.py preview
powershell -File start-listener.ps1 -Python "C:\path\to\python.exe"
python operations.py send
```

`preview`는 미리보기, `send`는 실제 발송입니다. 이후 `run-watch.ps1`을 10분마다 실행하도록 Codex나 Windows 작업 스케줄러에 등록합니다. `watch`는 새 수집 완료 여부를 확인합니다. **파일 복사만으로 예약이 이전되지는 않습니다.** `collection_check_minutes` 값만 바꿔도 예약 주기가 자동 변경되지는 않습니다.

Windows 예약 예시: 프로그램 `powershell.exe`, 인수 `-NoProfile -File "<이 폴더>\run-watch.ps1" -Python "<python.exe 경로>"`, 시작 위치 `<이 폴더>`. 로그인 계정에서 10분 반복으로 설정합니다. 결과에 `needs_attention`(점검 필요)이 있으면 원인을 확인합니다.

## 기록과 예외

- `output/live/alerts.sqlite`가 확인·발송 기록 파일입니다. 재시작할 때 삭제하지 않습니다. 운영을 옮길 때는 실행을 멈추고 DB를 백업해 별도로 전달합니다.
- 이미 긴급했던 사건은 일정이 연기되거나 사라져도 확인 전까지 유지합니다. 단순 문구·숫자 표기 수정은 확인을 해제하지 않습니다.
- 미확정 사건은 공식 출처·대상·명시 일정이 있고, 사유가 내부 사용 여부·계정 적용 조건·일부 원문 재검증에 한정될 때만 신규 알림에 포함합니다. 정확한 허용 목록은 `alerts.py`의 `event_eligible`에 있습니다.
- 새 수집 결과를 받을 때 같은 실행의 `github-pages` 결과물에서 `cards.json`(대시보드 판정)도 받아 `output/github-cache/<실행>/cards.json`에 둡니다. 읽기 권한은 수집 결과와 같습니다. 없거나 읽을 수 없으면 기존 규칙(7일 제한 포함)으로 보내고, Slack 머리말·실행 결과의 `cards_warning`·`send-preview.txt` 첫 줄에 사유를 적습니다. `source-status.json`의 `cards_error`에도 코드가 남습니다. `github-pages` 결과물은 1일만 보관되므로 PC가 하루 넘게 꺼져 있었다면 `pages_artifact_expired`로 대체 동작을 합니다.
- 수집 실패 시 기존 미확인 건에 경고를 붙여 재알림합니다. 수집 진행 중에는 기다리되 종일 끝나지 않으면 23시 이후 기존 건만 보냅니다.
- `sending`/`unknown`은 발송 결과를 확정하지 못한 상태입니다. 실제 Slack 메시지와 대조하기 전에는 자동 재전송하지 않습니다.
- Slack의 과거 메시지를 삭제하는 기능은 없습니다. 대시보드 버튼은 사건이 든 대시보드 카드로 열리고, 카드를 찾지 못하면 메인 페이지로 열립니다.
