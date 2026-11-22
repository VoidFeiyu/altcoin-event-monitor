"""Translations for the desktop interface and exported alerts."""

import locale
import re


LANGUAGES = {
    'zh-Hans': '简体中文',
    'en': 'English',
    'ja': '日本語',
    'ko': '한국어',
    'es': 'Español',
}
_TRANSLATED_LANGUAGES = ('en', 'ja', 'ko', 'es')

GUIDE_TEXT = (
    "价量版 A&E：现货最近1小时成交额至少为此前30天中位数的2倍，且达到P95；同时1小时绝对涨跌达到此前30天P95。\n\n"
    "完整版 A / OB / C / E：加入4小时OI数量变化至少10%且P95、按实际结算间隔归一化的已结算费率极端状态；至少满足两项且包含A或OB。OI和费率按5分钟延迟使用。资料缺失时只显示“完整版不足”，不会把价量版冒充完整版。\n\n"
    "结果：相对预警价格达到冻结门槛，同方向连续3根实际成交5分钟K线收盘确认。3 / 7 / 14天分别追踪，已发生可以确认；窗口没走完不会判未达标。数据缺口会明确标出。\n\n"
    "市场参考价：7天、30天成交金额÷成交币数量；前7天高低点；近似30天POC与70%成交密集带。成交分布将每根5分钟的全部成交量放到该根实际均价所在的0.5%对数价桶，仅为近似，区间不是成本置信区间。它们不是庄家或当前持有者的成本。\n\n"
    "价格来自Alpha USDT现货。零成交旧价不触发预警；未收盘K线不参与计算。每条预警保存行情时刻和实际扫描时刻。程序仅判断最近收盘，不把停机期间的历史信号当作实时提醒。\n\n"
    "程序不会开机自动启动。关闭窗口停止监控；最小化后仍可运行。需要电脑保持运行、网络正常。历史补齐以最近32天为主，长期停机的旧缺口不保证全部补回。\n\n"
    "部分官方合约接口可能返回451；软件显示限制，不更换地区出口或代理绕过。缺少OI时价量版可独立运行。没有交易、账户、私钥或对外消息接口。\n\n"
    "本机数据目录：{0}\n规则版本：alpha-events-v1  ·  预警是观察线索，不确定涨跌方向。"
)

_GUIDES = (
    (
        "Spot model A&E. The latest hour’s quote volume must reach twice the median and at least the 95th percentile of the previous 30 days. Absolute one-hour price change must also reach its 95th percentile.\n\n"
        "Full model A / OB / C / E. OB requires a four-hour increase in open interest quantity of at least 10% and its 95th percentile. C checks extreme settled funding normalized by the settlement interval. At least two signals must fire, including A or OB. Open interest and funding are delayed five minutes. Missing derivatives data disables the full model.\n\n"
        "Outcomes. Three consecutive traded five-minute candles must close beyond the saved alert threshold in the same direction. Follow-up windows are 3, 7 and 14 days. A hit can be confirmed early. Open windows and data gaps are shown separately.\n\n"
        "Market references. The app shows 7- and 30-day volume-weighted averages, the prior seven-day high and low, and an approximate 30-day POC and 70% volume area. Each candle's volume is assigned to a 0.5% logarithmic bucket at its traded average. These figures describe the market’s historical trades.\n\n"
        "Data. Prices come from Alpha USDT spot markets. Only closed candles with actual trades can trigger alerts. Each alert records candle and scan times. Backfilled alerts are excluded from live records.\n\n"
        "Operation. Start manually and leave the PC online. Minimizing keeps the monitor running. Closing stops it. Backfill covers roughly 32 days. Older gaps may remain.\n\n"
        "Derivatives. Official endpoints can return HTTP 451. The spot model runs independently when open interest is unavailable. The app uses public, read-only endpoints.\n\n"
        "Local data: {0}\nRule version: alpha-events-v1 · Alerts flag unusual activity. Direction is observed afterward."
    ),
    (
        "現物モデル A&E。直近1時間の売買代金が過去30日間の中央値の2倍以上かつ95パーセンタイル以上で、1時間の絶対価格変化も95パーセンタイル以上の場合に通知します。\n\n"
        "完全モデル A / OB / C / E。OBは4時間の建玉数量の増加が10%以上かつ95パーセンタイル以上、Cは実際の決済間隔で正規化した確定済み資金調達率の極端値です。AまたはOBを含む2項目以上が必要です。建玉と資金調達率には5分の遅延を適用します。データ不足時は完全モデルを停止します。\n\n"
        "結果。実際に取引のあった5分足が同方向で3本連続して通知時のしきい値を超えて確定すると達成です。3日、7日、14日の窓で追跡し、期間途中でも達成を確認できます。進行中の窓と欠損データは個別に表示します。\n\n"
        "市場参考値。7日・30日の出来高加重平均価格、直前7日の高値・安値、概算の30日POCと70%出来高帯を表示します。各足の出来高を取引平均価格の0.5%対数価格帯に割り当てた概算です。\n\n"
        "データ。Alpha USDTの現物価格を使います。実際に取引のあった確定足のみ通知を発生させます。足とスキャンの時刻を保存し、停止中の履歴はリアルタイム通知に追加しません。\n\n"
        "操作。手動で開始し、PCをオンラインにしてください。最小化しても監視を継続し、閉じると終了します。補完は直近32日間が中心で、古い欠損は残る場合があります。\n\n"
        "デリバティブ。公式APIがHTTP 451を返す場合があります。建玉データが使えなくても現物モデルは独立して動作します。公開の読み取り専用APIを使用します。\n\n"
        "ローカルデータ: {0}\nルール版: alpha-events-v1 · 異常を知らせ、方向は事後に確認します。"
    ),
    (
        "현물 모델 A&E. 최근 1시간 거래대금이 이전 30일 중앙값의 2배 이상이며 95백분위수 이상이어야 합니다. 1시간 절대 가격 변동률도 자체 95백분위수에 도달해야 합니다.\n\n"
        "전체 모델 A / OB / C / E. OB는 4시간 미결제약정 수량 증가율이 10% 이상이면서 95백분위수 이상인 경우입니다. C는 정산 주기로 정규화한 실제 펀딩비의 극단값입니다. A 또는 OB를 포함해 두 항목 이상이 필요합니다. 미결제약정과 펀딩비는 5분 지연을 적용합니다. 파생상품 데이터가 부족하면 전체 모델은 비활성화됩니다.\n\n"
        "결과. 실제 거래가 있었던 5분 봉 3개가 같은 방향으로 저장된 임계값을 넘어 마감하면 달성입니다. 3일, 7일, 14일을 따로 추적하고 기간 종료 전에도 달성을 확인할 수 있습니다. 추적 중과 데이터 공백은 따로 표시됩니다.\n\n"
        "시장 참고가. 7일·30일 거래량 가중 평균가, 직전 7일 고가·저가, 추정 30일 POC 및 70% 거래량 구간을 표시합니다. 각 봉의 거래량 전체를 실제 평균 거래가가 속하는 0.5% 로그 가격 구간에 배정한 추정치입니다.\n\n"
        "데이터. 가격은 Alpha USDT 현물에서 가져옵니다. 실거래가 있는 마감 봉만 알림을 생성할 수 있습니다. 봉 시각과 스캔 시각을 저장합니다. 중단 기간에 보충한 과거 알림은 실시간 기록에서 제외됩니다.\n\n"
        "실행. 수동으로 시작하고 PC를 온라인 상태로 유지하세요. 최소화하면 계속 실행되고 닫으면 종료됩니다. 최근 32일 데이터의 보충을 시도하며 오래된 공백은 남을 수 있습니다.\n\n"
        "파생상품. 공식 API가 HTTP 451을 반환할 수 있습니다. 미결제약정 데이터가 없어도 현물 모델은 독립적으로 작동합니다. 공개 읽기 전용 API를 사용합니다.\n\n"
        "로컬 데이터: {0}\n규칙 버전: alpha-events-v1 · 알림은 이상 움직임을 표시하며 방향은 사후 관찰합니다."
    ),
    (
        "Modelo al contado A&E. El volumen cotizado de la última hora debe alcanzar el doble de la mediana y el percentil 95 de los 30 días anteriores. La variación absoluta de una hora también debe alcanzar su percentil 95.\n\n"
        "Modelo completo A / OB / C / E. OB requiere que la cantidad de interés abierto aumente un 10% o más en cuatro horas y alcance el percentil 95. C detecta financiación liquidada extrema y normalizada por su intervalo. Se requieren dos señales, incluida A u OB. El interés abierto y la financiación se aplican con cinco minutos de retraso. Si faltan datos, el modelo completo se desactiva.\n\n"
        "Resultados. Tres velas consecutivas de cinco minutos con operaciones reales deben cerrar más allá del umbral guardado, en la misma dirección. Se siguen ventanas de 3, 7 y 14 días. Un acierto puede confirmarse antes del cierre. Las ventanas abiertas y los huecos de datos se muestran por separado.\n\n"
        "Referencias. Se muestran precios medios ponderados de 7 y 30 días, máximos y mínimos de los 7 días previos, y POC y área de valor del 70% aproximados de 30 días. El volumen de cada vela se asigna al tramo logarítmico del 0,5% de su precio medio negociado.\n\n"
        "Datos. Los precios provienen de Alpha al contado frente a USDT. Solo las velas cerradas con operaciones pueden generar alertas. Se guardan las horas de la vela y del escaneo. Los datos recuperados durante una interrupción no generan alertas retroactivas.\n\n"
        "Uso. Inicia el monitor manualmente y mantén el equipo conectado. Al minimizar, sigue funcionando. Al cerrar, se detiene. Se intenta recuperar unos 32 días de datos. Pueden quedar huecos anteriores.\n\n"
        "Derivados. Los nodos oficiales pueden devolver HTTP 451. El modelo al contado funciona sin datos de interés abierto. El monitor usa interfaces públicas de solo lectura.\n\n"
        "Datos locales: {0}\nVersión: alpha-events-v1 · Las alertas indican actividad inusual. La dirección se observa después."
    ),
)

# Translations are listed in the order above.
_MESSAGES = {
    "四币异动观察站 · 测试版": (
        "Four-token event monitor · Beta",
        "4銘柄の急変動モニター · ベータ",
        "4종목 이상 움직임 모니터 · 베타",
        "Monitor de cuatro altcoins · Beta",
    ),
    "异动观察站": (
        "Event monitor",
        "急変動モニター",
        "이상 움직임 모니터",
        "Monitor de anomalías",
    ),
    "  RAVE / BSB / EVAA / LAB  ·  本地测试版": (
        "  RAVE / BSB / EVAA / LAB  ·  Local beta",
        "  RAVE / BSB / EVAA / LAB  ·  ローカル版",
        "  RAVE / BSB / EVAA / LAB  ·  로컬 베타",
        "  RAVE / BSB / EVAA / LAB  ·  Beta local",
    ),
    "已暂停 · 关闭窗口即停止": (
        "Paused · Close window to stop",
        "一時停止中 · ウィンドウを閉じると終了",
        "일시 중지 · 창을 닫으면 종료",
        "En pausa · Cierra la ventana para salir",
    ),
    "观察后续是否真的出现大变动  ·  现货量价与完整版分别判断  ·  时间均为香港时间": (
        "Tracks price swings after alerts · Spot and full models are evaluated separately · Hong Kong time",
        "通知後の急変動を追跡 · 現物モデルと完全モデルは個別に判定 · 香港時間",
        "알림 이후 가격 변동 추적 · 현물 모델과 전체 모델은 별도 판정 · 홍콩 시간",
        "Seguimiento de cambios tras las alertas · Modelos separados · Hora de Hong Kong",
    ),
    "开始监控": (
        "Start monitor",
        "監視を開始",
        "모니터링 시작",
        "Iniciar monitor",
    ),
    "立即扫描": (
        "Scan now",
        "今すぐスキャン",
        "지금 스캔",
        "Escanear ahora",
    ),
    "导出实时记录": (
        "Export alerts",
        "通知を出力",
        "알림 내보내기",
        "Exportar alertas",
    ),
    "  事件门槛": (
        "  Event threshold",
        "  変動しきい値",
        "  변동 임계값",
        "  Umbral",
    ),
    "本机提示音": (
        "Sound alerts",
        "通知音",
        "알림음",
        "Alerta sonora",
    ),
    "5分钟扫描 · 每币每模型冷却24小时": (
        "5 min scans · 24 h cooldown per token and model",
        "5分間隔 · 銘柄・モデルごとに24時間の間隔",
        "5분 간격 · 종목·모델별 24시간 재알림 제한",
        "Escaneo cada 5 min · 24 h de espera por token y modelo",
    ),
    "等待扫描": (
        "Waiting for scan",
        "スキャン待ち",
        "스캔 대기",
        "En espera",
    ),
    "量比 —   24h —": (
        "Vol ratio —   24h —",
        "出来高比 —   24h —",
        "거래량 비율 —   24h —",
        "Ratio vol. —   24 h —",
    ),
    "K线 —": (
        "Candle —",
        "足 —",
        "캔들 —",
        "Vela —",
    ),
    "正在读取已有历史基准…": (
        "Reading historical baseline…",
        "過去の基準データを読み込み中…",
        "과거 기준 데이터 읽는 중…",
        "Cargando datos históricos…",
    ),
    "初始化…": (
        "Starting…",
        "起動中…",
        "시작 중…",
        "Iniciando…",
    ),
    "实时记录与结果": (
        "Live alerts and outcomes",
        "リアルタイム通知と結果",
        "실시간 알림과 결과",
        "Alertas y resultados",
    ),
    "历史回放案例": (
        "Historical replays",
        "過去のリプレイ",
        "과거 리플레이",
        "Repeticiones históricas",
    ),
    "规则与数据状态": (
        "Rules and data",
        "ルールとデータ",
        "규칙과 데이터",
        "Reglas y datos",
    ),
    "币种": (
        "Token",
        "銘柄",
        "종목",
        "Token",
    ),
    "预警模型": (
        "Model",
        "モデル",
        "모델",
        "Modelo",
    ),
    "预警时间": (
        "Alert time",
        "通知時刻",
        "알림 시각",
        "Hora de alerta",
    ),
    "冻结价格": (
        "Alert price",
        "通知時価格",
        "알림 가격",
        "Precio inicial",
    ),
    "触发依据": (
        "Triggers",
        "検出条件",
        "감지 조건",
        "Señales",
    ),
    "3天": (
        "3 days",
        "3日",
        "3일",
        "3 días",
    ),
    "7天": (
        "7 days",
        "7日",
        "7일",
        "7 días",
    ),
    "14天": (
        "14 days",
        "14日",
        "14일",
        "14 días",
    ),
    "尚无本机实时预警。历史案例在另一个页面；未触发时不会填造记录。": (
        "No live alerts saved on this computer.",
        "この端末にはリアルタイム通知がまだありません。",
        "이 기기에 저장된 실시간 알림이 없습니다.",
        "Aún no hay alertas en tiempo real guardadas.",
    ),
    "点击上方币种卡片查看已保存行情与市场参考价。": (
        "Select a token above to view stored prices and market references.",
        "上の銘柄を選ぶと保存済みの価格と市場の参考値を表示します。",
        "위에서 종목을 선택하면 저장된 시세와 시장 참고가를 볼 수 있습니다.",
        "Selecciona un token para ver sus precios guardados y referencias de mercado.",
    ),
    "实际事件": (
        "Observed event",
        "実際の変動",
        "실제 변동",
        "Evento observado",
    ),
    "回放预警时间": (
        "Replay alert time",
        "再現通知時刻",
        "리플레이 알림 시각",
        "Hora de alerta simulada",
    ),
    "当时价格": (
        "Initial price",
        "当時の価格",
        "당시 가격",
        "Precio inicial",
    ),
    "确认价": (
        "Confirmed at",
        "確認価格",
        "확인 가격",
        "Precio confirmado",
    ),
    "后续变化": (
        "Price change",
        "その後の変動",
        "이후 변동",
        "Variación",
    ),
    "7天完整": (
        "7 days complete",
        "7日間完了",
        "7일 완료",
        "7 días completos",
    ),
    "历史回放，不是当时实时发送的预警。案例按后续表现挑选，不能代表整体命中率。": (
        "These replays were selected by outcome. They show historical examples and do not measure the overall hit rate.",
        "リプレイは事後の結果で選んだ過去の例です。全体の的中率を示すものではありません。",
        "리플레이는 이후 결과로 선별한 과거 사례입니다. 전체 적중률을 나타내지 않습니다.",
        "Estos ejemplos históricos se eligieron según su resultado y no miden la tasa de acierto global.",
    ),
    "选择案例查看冻结价格、触发条件及实际结果。": (
        "Select a replay to view the initial price, triggers and observed outcome.",
        "事例を選ぶと通知時価格、検出条件、実際の結果を表示します。",
        "사례를 선택하면 당시 가격, 감지 조건, 실제 결과를 볼 수 있습니다.",
        "Selecciona un caso para ver el precio inicial, las señales y el resultado.",
    ),
    "历史Alpha现货价格 · 回放展示": (
        "Historical Alpha spot price · Replay",
        "Alpha現物の過去価格 · リプレイ",
        "Alpha 현물 과거 가격 · 리플레이",
        "Precio histórico en Alpha · Repetición",
    ),
    "上涨已确认": (
        "Rise confirmed",
        "上昇を確認",
        "상승 확인",
        "Subida confirmada",
    ),
    "下跌已确认": (
        "Drop confirmed",
        "下落を確認",
        "하락 확인",
        "Bajada confirmada",
    ),
    "是": (
        "Yes",
        "はい",
        "예",
        "Sí",
    ),
    "尚未结束": (
        "In progress",
        "進行中",
        "진행 중",
        "En curso",
    ),
    GUIDE_TEXT: _GUIDES,
    "新门槛仅用于后续新预警，已有记录保持原门槛。": (
        "The new threshold applies to future alerts. Saved alerts retain their original threshold.",
        "新しいしきい値は今後の通知に適用されます。保存済みの通知には元の値が残ります。",
        "새 임계값은 이후 알림에 적용됩니다. 기존 알림은 원래 값을 유지합니다.",
        "El nuevo umbral se aplica a las alertas futuras. Las guardadas conservan su umbral original.",
    ),
    "已暂停 · 当前请求结束后停止": (
        "Paused · Stops after current request",
        "一時停止 · 現在の要求が終わると停止",
        "일시 중지 · 현재 요청 후 정지",
        "En pausa · Se detiene tras la solicitud actual",
    ),
    "暂停监控": (
        "Pause monitor",
        "監視を一時停止",
        "모니터링 일시 중지",
        "Pausar monitor",
    ),
    "监控中 · 关闭窗口即停止": (
        "Monitoring · Close window to stop",
        "監視中 · ウィンドウを閉じると終了",
        "모니터링 중 · 창을 닫으면 종료",
        "En ejecución · Cierra la ventana para salir",
    ),
    "完整版": (
        "Full",
        "完全モデル",
        "전체 모델",
        "Completo",
    ),
    "价量版": (
        "Spot",
        "現物モデル",
        "현물 모델",
        "Al contado",
    ),
    "新预警：{0} · {1}": (
        "New alert: {0} · {1}",
        "新規通知 {0} · {1}",
        "새 알림: {0} · {1}",
        "Nueva alerta: {0} · {1}",
    ),
    "本轮完成 · ": (
        "Scan finished · ",
        "スキャン完了 · ",
        "스캔 완료 · ",
        "Escaneo completado · ",
    ),
    "下轮扫描 {0} 秒后": (
        "Next scan in {0} s",
        "次のスキャンまで {0} 秒",
        "{0}초 후 다음 스캔",
        "Próximo escaneo en {0} s",
    ),
    "扫描中…": (
        "Scanning…",
        "スキャン中…",
        "스캔 중…",
        "Escaneando…",
    ),
    "按需启动 · 本机保存": (
        "Start on demand · Stored locally",
        "手動で開始 · 端末に保存",
        "필요할 때 시작 · 로컬 저장",
        "Inicio manual · Guardado local",
    ),
    "尚未扫描": (
        "Not scanned",
        "未スキャン",
        "스캔 전",
        "Sin escanear",
    ),
    "历史缓存 · 等待新鲜行情": (
        "Cached · Waiting for fresh data",
        "キャッシュ済み · 最新データを待機",
        "캐시 사용 · 최신 시세 대기",
        "En caché · A la espera de datos nuevos",
    ),
    "量比 {0}    24h {1}": (
        "Vol ratio {0}    24h {1}",
        "出来高比 {0}    24h {1}",
        "거래량 비율 {0}    24h {1}",
        "Ratio vol. {0}    24 h {1}",
    ),
    "完整版就绪": (
        "Full model ready",
        "完全モデル準備完了",
        "전체 모델 준비",
        "Modelo completo listo",
    ),
    "完整版不足": (
        "Full model unavailable",
        "完全モデル利用不可",
        "전체 모델 사용 불가",
        "Modelo completo no disponible",
    ),
    "K线 {0}  ·  {1}": (
        "Candle {0} · {1}",
        "足 {0} · {1}",
        "캔들 {0} · {1}",
        "Vela {0} · {1}",
    ),
    "{0}现货：{1}": (
        "{0} spot: {1}",
        "{0} 現物: {1}",
        "{0} 현물: {1}",
        "{0} al contado: {1}",
    ),
    "初次使用需要30天历史基准；扫描会读取缓存并补齐。": (
        "The first scan builds a 30-day baseline using cached data and recent history.",
        "初回スキャンでキャッシュと直近データから30日間の基準を作成します。",
        "첫 스캔에서 캐시와 최근 시세로 30일 기준을 만듭니다.",
        "El primer escaneo crea una base de 30 días con la caché y el historial reciente.",
    ),
    "新鲜现货 {0}/4   ·   完整版就绪 {1}/4   ·   {2}": (
        "Fresh spot data {0}/4 · Full model ready {1}/4 · {2}",
        "最新現物データ {0}/4 · 完全モデル準備完了 {1}/4 · {2}",
        "최신 현물 {0}/4 · 전체 모델 준비 {1}/4 · {2}",
        "Datos vigentes {0}/4 · Modelo completo listo {1}/4 · {2}",
    ),
    "已保存 {0} 条本机实时预警；结果按各自冻结门槛判断。": (
        "{0} live alerts saved. Outcomes use the threshold recorded with each alert.",
        "通知 {0}件を保存済み。結果は通知ごとのしきい値で判定します。",
        "실시간 알림 {0}건 저장. 결과는 각 알림의 임계값으로 판정합니다.",
        "{0} alertas guardadas. Cada resultado usa el umbral de su alerta.",
    ),
    "{0}  ·  成交均价 7天 {1} / 30天 {2}  ·  近似POC {3} / 70%成交带 {4}—{5}\n前7天高低 {6} / {7}  ·  市场历史参考，非庄家成本": (
        "{0} · VWAP 7 d {1} / 30 d {2} · Approx. POC {3} / 70% value area {4}–{5}\nPrior 7 d high/low {6} / {7} · Historical market references",
        "{0} · VWAP 7日 {1} / 30日 {2} · 概算POC {3} / 70%出来高帯 {4}–{5}\n過去7日高値/安値 {6} / {7} · 過去の市場参考値",
        "{0} · VWAP 7일 {1} / 30일 {2} · 추정 POC {3} / 70% 거래량 구간 {4}–{5}\n이전 7일 고가/저가 {6} / {7} · 과거 시장 참고가",
        "{0} · VWAP 7 d {1} / 30 d {2} · POC aprox. {3} / área de valor del 70 % {4}–{5}\nMáx./mín. de 7 d previos {6} / {7} · Referencias históricas",
    ),
    "{0} · 最近3天已保存Alpha现货收盘": (
        "{0} · Stored Alpha spot closes for the last 3 days",
        "{0} · 過去3日間のAlpha現物終値",
        "{0} · 최근 3일간 저장된 Alpha 현물 종가",
        "{0} · Cierres de Alpha de los últimos 3 días",
    ),
    "选择币种查看已保存行情": (
        "Select a token to view stored prices",
        "銘柄を選んで保存済み価格を表示",
        "종목을 선택해 저장된 시세 확인",
        "Selecciona un token para ver sus precios",
    ),
    "等待可用的真实行情记录": (
        "Waiting for market data",
        "市場データを待機中",
        "시세 데이터 대기 중",
        "Esperando datos de mercado",
    ),
    "{0} · {1} · 行情 {2} · 实际扫描 {3} · 冻结门槛 ±{4:.0%} · 确认方向为事后结果": (
        "{0} · {1} · Candle {2} · Scanned {3} · Threshold ±{4:.0%} · Direction is observed afterward",
        "{0} · {1} · 足 {2} · スキャン {3} · しきい値 ±{4:.0%} · 方向は事後に確認",
        "{0} · {1} · 캔들 {2} · 스캔 {3} · 임계값 ±{4:.0%} · 방향은 사후 확인",
        "{0} · {1} · Vela {2} · Escaneo {3} · Umbral ±{4:.0%} · Dirección observada después",
    ),
    "{0} · 该预警前1天至后14天已保存路径": (
        "{0} · Stored prices from 1 day before to 14 days after the alert",
        "{0} · 通知の1日前から14日後までの保存価格",
        "{0} · 알림 1일 전부터 14일 후까지 저장된 가격",
        "{0} · Precios guardados desde 1 día antes hasta 14 días después de la alerta",
    ),
    "历史回放 {0} · {1} · 预警价 {2} → 确认价 {3} ({4}) · 确认 {5} · 预警前24h {6}。仅展示既有研究，不写入实时记录。": (
        "Replay {0} · {1} · Initial {2} → confirmed {3} ({4}) · At {5} · Prior 24h {6}. Historical research only.",
        "リプレイ {0} · {1} · 当時 {2} → 確認時 {3} ({4}) · 時刻 {5} · 直前24h {6}。過去の調査例。",
        "리플레이 {0} · {1} · 당시 {2} → 확인 {3} ({4}) · 시각 {5} · 이전 24시간 {6}. 과거 연구 사례.",
        "Repetición {0} · {1} · Inicial {2} → confirmado {3} ({4}) · Hora {5} · 24 h previas {6}. Caso histórico.",
    ),
    "{0} · 历史Alpha现货路径（非实时）": (
        "{0} · Historical Alpha spot prices",
        "{0} · Alpha現物の過去価格",
        "{0} · Alpha 현물 과거 가격",
        "{0} · Precios históricos al contado en Alpha",
    ),
    "导出本机实时预警": (
        "Export locally recorded alerts",
        "端末に保存した通知を出力",
        "로컬에 저장된 알림 내보내기",
        "Exportar alertas guardadas localmente",
    ),
    "CSV文件": (
        "CSV file",
        "CSVファイル",
        "CSV 파일",
        "Archivo CSV",
    ),
    "四币异动预警.csv": (
        "altcoin-alerts.csv",
        "急変動通知.csv",
        "이상-움직임-알림.csv",
        "alertas-altcoins.csv",
    ),
    "已导出：": (
        "Exported: ",
        "出力先: ",
        "내보낸 위치: ",
        "Exportado: ",
    ),
    "程序已经运行，请从任务栏打开现有窗口。": (
        "The monitor is already running. Open its window from the taskbar.",
        "すでに起動しています。タスクバーから既存のウィンドウを開いてください。",
        "이미 실행 중입니다. 작업 표시줄에서 기존 창을 여세요.",
        "El monitor ya está en ejecución. Abre su ventana desde la barra de tareas.",
    ),
    "等待现货历史": (
        "Waiting for spot history",
        "現物の履歴待ち",
        "현물 이력 대기",
        "Esperando historial al contado",
    ),
    "正常观察": (
        "Monitoring",
        "監視中",
        "관찰 중",
        "En observación",
    ),
    "30天历史不足或数据不完整": (
        "Incomplete 30-day history",
        "30日履歴が不足",
        "30일 이력 부족",
        "Historial de 30 días incompleto",
    ),
    "行情过期，停止预警": (
        "Data stale · Alerts paused",
        "データが古いため通知停止",
        "시세 만료 · 알림 중지",
        "Datos vencidos · Alertas en pausa",
    ),
    "当前K线无实际成交，停止预警": (
        "No trades · Alerts paused",
        "約定がないため通知停止",
        "실거래 없음 · 알림 중지",
        "Sin operaciones · Alertas en pausa",
    ),
    "满足预警条件": (
        "Alert conditions met",
        "通知条件を満たしました",
        "알림 조건 충족",
        "Condiciones de alerta cumplidas",
    ),
    "现货获取失败，停止预警": (
        "Spot feed unavailable",
        "現物データの取得に失敗",
        "현물 시세 가져오기 실패",
        "Datos al contado no disponibles",
    ),
    "本轮连接失败": (
        "Connection failed",
        "接続に失敗",
        "연결 실패",
        "Error de conexión",
    ),
    "待观察": (
        "Pending",
        "追跡中",
        "추적 중",
        "Pendiente",
    ),
    "双向已达标": (
        "Both hit",
        "上下達成",
        "양방향 달성",
        "Ambas alcanzadas",
    ),
    "上涨已达标": (
        "Rise hit",
        "上昇達成",
        "상승 달성",
        "Subida alcanzada",
    ),
    "下跌已达标": (
        "Drop hit",
        "下落達成",
        "하락 달성",
        "Bajada alcanzada",
    ),
    "数据缺口": (
        "Data gap",
        "データ欠損",
        "데이터 공백",
        "Datos faltantes",
    ),
    "未达标": (
        "No hit",
        "未達成",
        "미달성",
        "No alcanzado",
    ),
    "现货放量": (
        "Spot volume spike",
        "現物出来高急増",
        "현물 거래량 급증",
        "Pico de volumen al contado",
    ),
    "OI异常增加": (
        "OI spike",
        "建玉急増",
        "미결제약정 급증",
        "Pico de interés abierto",
    ),
    "已结算费率异常": (
        "Settled funding anomaly",
        "確定資金調達率の異常",
        "정산 펀딩비 이상",
        "Financiación liquidada anómala",
    ),
    "短线涨跌异常": (
        "Short-term price swing",
        "短期価格の急変動",
        "단기 가격 급변",
        "Variación brusca a corto plazo",
    ),
    "、": (
        ", ",
        "、",
        ", ",
        ", ",
    ),
    "模型": (
        "Model",
        "モデル",
        "모델",
        "Modelo",
    ),
    "预警K线结束UTC": (
        "Alert candle close (UTC)",
        "通知の足終値時刻（UTC）",
        "알림 캔들 마감(UTC)",
        "Cierre de la vela (UTC)",
    ),
    "实际扫描UTC": (
        "Scan time (UTC)",
        "スキャン時刻（UTC）",
        "스캔 시각(UTC)",
        "Hora de escaneo (UTC)",
    ),
    "预警价USDT": (
        "Alert price (USDT)",
        "通知時価格（USDT）",
        "알림 가격(USDT)",
        "Precio de alerta (USDT)",
    ),
    "冻结幅度": (
        "Saved threshold",
        "保存済みしきい値",
        "저장된 임계값",
        "Umbral guardado",
    ),
    "3天结果": (
        "3-day outcome",
        "3日間の結果",
        "3일 결과",
        "Resultado a 3 días",
    ),
    "7天结果": (
        "7-day outcome",
        "7日間の結果",
        "7일 결과",
        "Resultado a 7 días",
    ),
    "14天结果": (
        "14-day outcome",
        "14日間の結果",
        "14일 결과",
        "Resultado a 14 días",
    ),
    "规则版本": (
        "Rule version",
        "ルール版",
        "규칙 버전",
        "Versión de reglas",
    ),
    "历史基准就绪，点击“开始监控”或“立即扫描”": (
        "Baseline ready. Start monitoring or scan now.",
        "基準データを準備済みです。監視を開始するか今すぐスキャンしてください。",
        "기준 데이터 준비 완료. 모니터링을 시작하거나 지금 스캔하세요.",
        "Base lista. Inicia el monitor o escanea ahora.",
    ),
    "本地历史读取失败；联网扫描会重新建立基准": (
        "Local history could not be read. An online scan will rebuild the baseline.",
        "履歴を読めませんでした。オンラインスキャンで基準を再作成します。",
        "로컬 이력을 읽지 못했습니다. 온라인 스캔에서 기준을 다시 만듭니다.",
        "No se pudo leer el historial local. Un escaneo en línea reconstruirá la base.",
    ),
    "核对代币标识与已收盘行情…": (
        "Checking token identities and closed candles…",
        "銘柄の識別情報と確定足を確認中…",
        "종목 식별자와 마감 캔들 확인 중…",
        "Comprobando tokens y velas cerradas…",
    ),
    "追踪已保存预警的3/7/14天实际结果…": (
        "Checking saved alerts at 3, 7 and 14 days…",
        "保存済み通知の3日・7日・14日の結果を確認中…",
        "저장된 알림의 3·7·14일 결과 확인 중…",
        "Comprobando resultados de alertas a 3, 7 y 14 días…",
    ),
    "Alpha 交易对目录格式异常": (
        "Invalid Alpha pair catalog",
        "Alpha取引ペア一覧の形式が無効",
        "잘못된 Alpha 거래쌍 목록 형식",
        "Catálogo de pares de Alpha no válido",
    ),
    "Alpha响应不完整，保留原数据等待重试": (
        "Alpha response incomplete. Existing data kept.",
        "Alphaの応答が不完全です。既存データを保持します。",
        "Alpha 응답이 불완전합니다. 기존 데이터를 유지합니다.",
        "Respuesta de Alpha incompleta. Se conservan los datos existentes.",
    ),
    "合约公开数据格式异常": (
        "Invalid public derivatives data format",
        "公開デリバティブデータの形式が無効",
        "파생상품 공개 데이터 형식 오류",
        "Formato de datos públicos de derivados no válido",
    ),
    "代币标识或状态变化，需要复核": (
        "Token identity or status changed. Review it.",
        "銘柄IDまたは状態が変更されました。確認してください。",
        "종목 식별자나 상태가 변경되었습니다. 확인이 필요합니다.",
        "Cambió la identidad o el estado del token. Revísalo.",
    ),
    "Alpha未遵守请求区间，停止本轮更新": (
        "Alpha returned data outside the requested range. Scan stopped.",
        "Alphaが要求範囲外のデータを返したため停止しました。",
        "Alpha가 요청 범위 밖의 데이터를 반환해 스캔을 중단했습니다.",
        "Alpha devolvió datos fuera del intervalo solicitado. Se detuvo el escaneo.",
    ),
    "代币身份未核验": (
        "Token identity not verified",
        "銘柄ID未確認",
        "종목 식별자 미검증",
        "Identidad del token sin verificar",
    ),
    "现货K线字段不完整": (
        "Incomplete candle fields",
        "足の項目が不足",
        "현물 캔들 필드 누락",
        "Campos de vela incompletos",
    ),
    "现货K线时间边界异常": (
        "Invalid candle time boundary",
        "足の時間境界が無効",
        "현물 캔들 시간 경계 오류",
        "Límite de hora de vela no válido",
    ),
    "现货K线包含无效数值": (
        "Invalid candle values",
        "足に無効な数値があります",
        "현물 캔들 값 오류",
        "Valores de vela no válidos",
    ),
    "现货OHLC或成交量异常": (
        "Invalid spot OHLC or volume",
        "現物OHLCまたは出来高が無効",
        "현물 OHLC 또는 거래량 오류",
        "OHLC o volumen al contado no válidos",
    ),
    "成交笔数异常": (
        "Invalid trade count",
        "約定件数が無効",
        "거래 건수 오류",
        "Número de operaciones no válido",
    ),
    "官方合约接口 HTTP {0}，完整版等待重试": (
        "Official derivatives API HTTP {0}. Waiting to retry.",
        "公式デリバティブAPI HTTP {0}。再試行待ち。",
        "공식 파생상품 API HTTP {0}. 재시도 대기 중.",
        "API oficial de derivados HTTP {0}. En espera de reintento.",
    ),
    "行情接口 HTTP {0}": (
        "Market API HTTP {0}",
        "市場データAPI HTTP {0}",
        "시세 API HTTP {0}",
        "API de mercado HTTP {0}",
    ),
    "行情请求失败：{0}": (
        "Market request failed: {0}",
        "市場データの要求に失敗: {0}",
        "시세 요청 실패: {0}",
        "Falló la consulta de mercado: {0}",
    ),
    "读取 {0} 已有历史，准备30天基准…": (
        "Reading stored {0} data for the 30-day baseline…",
        "{0} の保存済みデータから30日間の基準を準備中…",
        "{0}의 저장 기록으로 30일 기준을 준비하는 중…",
        "Leyendo historial de {0} para preparar la base de 30 días…",
    ),
    "正在扫描 {0} · 已收盘5分钟K线": (
        "Scanning {0} · Closed 5-minute candles",
        "{0} をスキャン中 · 確定済み5分足",
        "{0} 스캔 중 · 마감된 5분 캔들",
        "Escaneando {0} · Velas cerradas de 5 min",
    ),
    "本轮未完成：{0}；已有记录已保留": (
        "Scan incomplete: {0}. Existing data kept.",
        "スキャン未完了: {0}。既存データを保持しました。",
        "스캔 미완료: {0}. 기존 기록을 유지했습니다.",
        "Escaneo incompleto: {0}. Se conservaron los datos existentes.",
    ),
}


def system_language():
    name = locale.getlocale()[0] or ''
    code = name.split('_')[0].lower()
    return code if code in _TRANSLATED_LANGUAGES else 'zh-Hans'


def translate(language, key, *args):
    translations = _MESSAGES.get(key)
    if translations and language in _TRANSLATED_LANGUAGES:
        key = translations[_TRANSLATED_LANGUAGES.index(language)]
    return key.format(*args) if args else key


# Old snapshots and worker events store source-language strings. Translate them at display time.
_DYNAMIC_MESSAGES = tuple((re.compile(pattern), template) for pattern, template in (
    (r'官方合约接口 HTTP (\d+)，完整版等待重试', '官方合约接口 HTTP {0}，完整版等待重试'),
    (r'行情接口 HTTP (\d+)', '行情接口 HTTP {0}'),
    (r'行情请求失败：(.*)', '行情请求失败：{0}'),
    (r'读取 (\S+) 已有历史，准备30天基准…', '读取 {0} 已有历史，准备30天基准…'),
    (r'正在扫描 (\S+) · 已收盘5分钟K线', '正在扫描 {0} · 已收盘5分钟K线'),
    (r'本轮未完成：([\s\S]*?)；已有记录已保留', '本轮未完成：{0}；已有记录已保留'),
))


def translate_message(language, message):
    for pattern, template in _DYNAMIC_MESSAGES:
        match = pattern.fullmatch(message)
        if match:
            args = (translate_message(language, match[1]),) if template.startswith('本轮未完成') else match.groups()
            return translate(language, template, *args)
    return translate(language, message)
