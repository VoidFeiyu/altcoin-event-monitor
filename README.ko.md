# 알트코인 이상 움직임 모니터

[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · 한국어 · [Español](README.es.md)

RAVE, BSB, EVAA, LAB을 감시하는 Windows 데스크톱 도구입니다. Binance Alpha 현물과 Binance 공개 파생상품 데이터를 읽으며 계정이나 API 키가 필요 없습니다. 수동으로 시작하면 5분마다 스캔하고 거래량과 가격 이상을 기록한 뒤 3일, 7일, 14일 결과를 추적합니다.

## 설치 및 실행

소스 코드를 내려받고 Tcl/Tk가 포함된 Python 3.12를 설치하세요. 프로젝트 폴더에서 PowerShell을 열고 실행합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

설치 후 `start-monitor.bat`을 두 번 클릭해도 실행할 수 있습니다. 언어 메뉴에서 한국어를 선택하면 설정이 저장됩니다. ‘모니터링 시작’으로 정기 스캔을 실행하거나 ‘지금 스캔’으로 한 번만 확인할 수 있습니다. 창을 닫으면 종료됩니다.

## 알림 규칙

- **A · 현물 거래량 급증.** 최근 1시간 거래대금이 직전 30일 시간별 중앙값의 2배 이상이며 95백분위수 이상입니다.
- **E · 가격 급변.** 최근 1시간 현물 가격 절대 변동률이 직전 30일 95백분위수 이상입니다.
- **OB · 미결제약정 급증.** 4시간 미결제약정 수량 증가율이 10% 이상이며 직전 30일 95백분위수 이상입니다.
- **C · 펀딩비 이상.** 정산 주기로 정규화한 실제 펀딩비가 양수이면 95백분위수 이상, 음수이면 5백분위수 미만입니다.

현물 모델은 A와 E를 모두 충족해야 합니다. 전체 모델은 A 또는 OB를 포함한 두 가지 이상을 충족해야 합니다. 종목과 모델별로 재알림 대기 시간은 24시간입니다. 파생상품 데이터가 부족해도 현물 모델은 독립적으로 작동합니다.

기본 결과 임계값은 20%이며 10%, 30%, 50%도 선택할 수 있습니다. 임계값은 알림마다 저장됩니다. 실거래가 있는 5분 캔들이 연속 세 번 같은 방향으로 이 기준에 도달한 채 마감하면 달성으로 판정합니다. 추적 중인 기간과 데이터 공백을 따로 표시합니다.

## 데이터 및 테스트

첫 스캔에서 최근 32일 현물 데이터를 보충하려고 시도합니다. 알림, 설정, 시세 캐시, 로그는 `data/alt_event_monitor/`에 저장됩니다. 알림은 CSV로 내보낼 수 있습니다. 과거 리플레이 목록은 로컬 연구 자료가 있을 때 표시됩니다.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor -v
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
