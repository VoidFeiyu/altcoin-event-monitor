# 알트코인 기회 분석

[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · 한국어 · [Español](README.es.md)

Binance 현물과 Alpha 활성 종목을 검색하고 관심 목록이나 전체 종목을 순환 스캔하는 Windows 도구입니다. 알림 후 3일, 7일, 14일 결과와 펀딩비, 온체인 가격차, LP 헤지를 확인할 수 있습니다. 공개 데이터만 사용합니다.

## 설치 및 실행

소스 코드를 내려받고 Tcl/Tk가 포함된 Python 3.12를 설치하세요. 프로젝트 폴더에서 PowerShell을 열고 실행합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

설치 후 `start-monitor.bat`을 두 번 클릭해도 실행할 수 있습니다. 언어 메뉴에서 한국어를 선택하면 설정이 저장됩니다. ‘모니터링 시작’으로 정기 스캔을 실행하거나 ‘지금 스캔’으로 한 번만 확인할 수 있습니다. 창을 닫으면 종료됩니다.

## 거래 기회 분석

- **펀딩비와 베이시스**. 빈칸이면 OKX USDT 현물과 선형 무기한 계약이 모두 있는 종목을 순환 스캔합니다. 쉼표로 구분해 종목을 지정할 수도 있습니다.
- **온체인 가격차**. 선택한 V3 풀과 대응하는 OKX 현물의 양방향 가격차를 비교하고 수수료와 추정 가스비를 차감합니다.
- **LP 헤지 재현**. 선택한 풀의 약 6·12·24시간을 재현합니다. 헤지 비율이 0%이면 풀 데이터만 사용합니다. 헤지에는 대응하는 OKX USDT 무기한 계약이 필요합니다.

앞의 두 페이지는 즉시 수집과 5분 간격 수집을 지원합니다. 선택한 근거는 각 페이지에서 JSON으로 내보낼 수 있습니다.

## 온체인 이벤트 재현

레이더 종목이나 알림에서 열거나, 종목을 선택하거나, Ethereum 또는 BNB Smart Chain ERC-20 계약을 입력합니다. Uniswap v3와 PancakeSwap v3에서 USDT 또는 USDC 직접 풀을 찾습니다. 알림 전후 3·5·10분이나 최근 완료 구간을 선택합니다. 호가와 거래 해시가 있는 이벤트를 별도로 표시합니다. 데이터 공백을 표시하며 JSON으로 내보낼 수 있습니다.

## 알림 규칙

- **A · 현물 거래량 급증.** 최근 1시간 거래대금이 직전 30일 시간별 중앙값의 2배 이상이며 95백분위수 이상입니다.
- **E · 가격 급변.** 최근 1시간 현물 가격 절대 변동률이 직전 30일 95백분위수 이상입니다.
- **OB · 미결제약정 급증.** 4시간 미결제약정 수량 증가율이 10% 이상이며 직전 30일 95백분위수 이상입니다.
- **C · 펀딩비 이상.** 정산 주기로 정규화한 실제 펀딩비가 양수이면 95백분위수 이상, 음수이면 5백분위수 미만입니다.

전체 레이더는 마감된 5분 봉으로 A와 E를 판정합니다. 종목별 알림 간격은 24시간입니다. OB와 C를 이용한 이전 알림도 확인할 수 있습니다.

기본 결과 임계값은 20%이며 10%, 30%, 50%도 선택할 수 있습니다. 임계값은 알림마다 저장됩니다. 실거래가 있는 5분 캔들이 연속 세 번 같은 방향으로 이 기준에 도달한 채 마감하면 달성으로 판정합니다. 추적 중인 기간과 데이터 공백을 따로 표시합니다.

## 데이터 및 테스트

전체 스캔은 한 번에 최대 24개를 처리하며 약 1분 후 저장한 위치에서 이어집니다. 첫 스캔에서 최근 32일 현물 데이터를 보충하려고 시도합니다. 알림, 설정, 시세 캐시, 로그는 `data/alt_event_monitor/`에 저장됩니다. 알림은 CSV로 내보낼 수 있습니다. 과거 리플레이 목록은 로컬 연구 자료가 있을 때 표시됩니다.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor tests.test_alt_opportunities tests.test_chain_replay -v
```

## 라이선스

[MIT](LICENSE)

## 자발적 후원

이 도구가 도움이 되었다면 자발적으로 후원할 수 있습니다. 모든 기능은 무료입니다.

| 네트워크 | 후원 수신 주소 |
| --- | --- |
| Ethereum | `0xEbc48077968d7Afda810603194b0412C35e3B655` |
| Bitcoin | `bc1qjj9dx0fwmzevwjffa55z6wp76egaztjee8ml08` |
| Solana | `2y11NNPRiJpSkLHR4zW23M67KuqxmpdTmhpKim46WtBn` |
| Tron | `TBUzeZCAuqEFuh1rUGmCnmGQoiCKkgHk4Z` |
| Stellar | `GDA4A4GN5WCMADT3GXXBKUEVIUQQPOPDVRX5B23TOVK2AGRV5LBHV545` |

보내기 전에 지갑에서 표에 표시된 네트워크를 선택했는지 확인하세요.
