KCP 탄소정책 확산 의사결정 대시보드 v7
================================================

이번 버전의 핵심
----------------
1. 사용자는 전국 동·읍·면 이름만 입력합니다.
2. 서버가 공개 법정동 코드 목록에서 실제 지역을 검색합니다.
3. 사용자가 같은 이름의 여러 지역 중 정확한 지역을 선택합니다.
4. 법정동 10자리 코드를 KCP가 사용하는 코드로 변환합니다.
   - ctp_cd = 시도 코드 앞 8자리
   - sig_cd = 시군구 코드 앞 8자리
   - emd_cd = 법정동 코드 앞 8자리
5. 서버가 KCP monthly API를 호출합니다.
   POST https://api.korea-carbon-project.org/region/compare/monthly
6. 선택한 KCP metric의 2020.01~2022.12 월별 값을 받습니다.
7. 탄소 v3 방식으로 즉시 계산합니다.
   - 최근 12개월 평균배출량
   - STL(period=12, robust=True) 기반 최근 12개월 Trend 기울기
   - 최근 12개월 YoY 증가월 비율 (v3의 pct_change(12) 정의)
8. 계산 결과를 지도·후보현황·시나리오 순위·민감도·제언에 반영합니다.

KCP metric 연결
---------------
- 에너지 제조업 및 건설업: en_fc_mc
- 농업: agr_luu
- 폐기물: wst_wiob

위 코드는 KCP 비교 페이지의 Network 요청에서 실제로 확인된 metric 코드입니다.
'에너지 제조업 및 건설업'의 en_fc_mc 연결은 코드 명칭과 기존 데이터 비교 목적에 맞춰 사용합니다.
농업/폐기물 코드는 KCP Network에서 확인된 해당 계열 코드로 연결한 것이며,
공식 API 문서가 공개된 상태는 아니므로 공모전 문서에는 'KCP 웹 서비스 호출 구조를 활용한 프로토타입'으로 표기하는 것을 권장합니다.

전국 지역 검색 데이터
---------------------
서버는 아래 공개 CSV를 필요할 때 받아 24시간 메모리 캐시합니다.
https://raw.githubusercontent.com/kr-legal-dong/kr-legal-dong/refs/heads/main/dong.csv

이 공개 저장소는 code.go.kr 기반 대한민국 법정동 데이터를 제공했던 저장소입니다.
저장소가 보관(archived) 상태이므로 최근 행정구역 개편이 있으면 일부 차이가 생길 수 있습니다.

Render 배포
-----------
이 폴더를 GitHub 저장소에 올린 다음 Render에서 Blueprint 또는 Web Service로 배포합니다.

가장 쉬운 방식:
1. GitHub 새 repository 생성
2. 이 폴더 안의 파일 전체 업로드
3. Render 로그인
4. New + -> Blueprint
5. GitHub repository 연결
6. render.yaml 인식 후 Apply

또는 Web Service 수동 설정:
- Build Command: pip install -r requirements.txt
- Start Command: python app.py
- Health Check Path: /api/health

배포가 끝나면
https://프로젝트명.onrender.com
형태의 공개 링크가 생기며, 사용자는 ZIP이나 Python 설치 없이 링크만 열면 됩니다.

중요
----
- KCP API는 공개 문서화된 API가 아니라 KCP 웹페이지 Network에서 확인된 호출입니다.
- 호출 방식이나 metric 코드가 KCP 사이트 개편으로 바뀌면 연동 코드를 수정해야 합니다.
- 서버는 브라우저 CORS를 우회하기 위해 인증을 속이는 것이 아니라,
  서버 측에서 일반 HTTP 요청으로 공개 웹 엔드포인트에 접근합니다.
- KCP API가 외부 서버 호출을 제한하면 자동 조회가 실패할 수 있습니다.
