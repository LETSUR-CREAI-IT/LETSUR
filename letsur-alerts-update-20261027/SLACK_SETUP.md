# Slack 앱 연결

`slack-app-manifest.json`은 **봇 설정을 재현하는 파일**입니다. 기존 앱·토큰·메시지 이력까지 옮기지는 않습니다.

1. [앱 만들기](https://api.slack.com/apps) → **From a manifest**에서 JSON을 붙여넣고 워크스페이스(팀 공간)를 선택합니다.
2. **Install to Workspace**로 설치하고 알림 채널에 봇을 초대합니다.
3. Bot User OAuth Token(봇 인증키)을 `.env`의 `SLACK_BOT_TOKEN`에 넣습니다.
4. **Basic Information → App-Level Tokens**에서 `connections:write` 권한의 토큰을 만들어 `SLACK_APP_TOKEN`에 넣습니다.
5. `.env`에 팀·채널 ID를 넣고 `operations.json`의 `app_id`를 새 앱 ID로 바꿉니다.
6. **Socket Mode**(버튼 클릭을 받는 연결)와 **Interactivity**(버튼 기능)가 켜졌는지 확인한 뒤 [실행 안내](RUNNING.md)를 따릅니다.

봇 권한은 `chat:write`(메시지 전송)입니다. 공개 요청 주소나 메시지 읽기 권한은 현재 기능에 필요 없습니다. 이 설정 파일은 재현용 템플릿이며 새 공간에서의 설치는 별도 검증이 필요합니다.

기존 테스트를 함께 볼 때는 팀원을 테스트 공간·채널에 초대하면 됩니다. 앱 설정도 관리할 사람은 **Collaborators**에 추가합니다. 토큰은 GitHub에 올리지 않습니다.

[Slack 공식 안내](https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests/)
