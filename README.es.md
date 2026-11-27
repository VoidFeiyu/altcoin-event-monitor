# Análisis de oportunidades en altcoins

[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · Español

Herramienta para Windows con alertas, financiación, base, diferenciales en cadena y simulación LP. Busca activos de Binance Spot y Alpha, vigila favoritos o recorre el catálogo y sigue alertas a 3, 7 y 14 días. Usa datos públicos sin cuenta ni clave de trading.

## Instalación y uso

Descarga el código e instala Python 3.12 con Tcl/Tk. Abre PowerShell desde el directorio del proyecto y ejecuta estos comandos.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

Después de la instalación también puedes abrir `start-monitor.bat`. Selecciona Español en el menú de idioma para guardar tu preferencia. Pulsa **Iniciar monitor** para activar los escaneos periódicos o **Escanear ahora** para hacer solo uno. Al cerrar la ventana se detiene el monitor.

## Análisis de oportunidades

- **Financiación y base.** Deja el campo vacío para recorrer los activos con contado y perpetuo lineal USDT en OKX, o introduce símbolos separados por comas.
- **Diferenciales en cadena.** Compara la misma cantidad en un pool V3 elegido y en el mercado correspondiente de OKX. Aplica comisiones y gas estimado.
- **Cobertura LP.** Usa el pool seleccionado durante unas 6, 12 o 24 horas. Una cobertura del 0% solo requiere datos del pool. La cobertura requiere el perpetuo USDT correspondiente en OKX.

Las dos primeras páginas admiten muestras puntuales o cada cinco minutos. Las tres exportan la evidencia seleccionada a JSON.

## Eventos en cadena

Abre desde un activo o alerta del radar, elige un token o introduce un contrato ERC-20 de Ethereum o BNB Smart Chain. Busca pools directos en USDT o USDC de Uniswap v3 y PancakeSwap v3. Selecciona 3, 5 o 10 minutos a cada lado del cierre de la alerta o el último intervalo completo. Las cotizaciones y los eventos con hashes se muestran por separado. Los datos ausentes se marcan y el resultado se exporta a JSON.

## Reglas de alerta

- **A · Pico de volumen al contado.** El volumen cotizado de la última hora alcanza el doble de la mediana horaria y el percentil 95 de los 30 días anteriores.
- **E · Variación de precio.** El cambio absoluto del precio al contado en la última hora alcanza el percentil 95 de los 30 días anteriores.
- **OB · Pico de interés abierto.** El crecimiento de la cantidad de interés abierto en cuatro horas alcanza el 10% y el percentil 95 de los 30 días anteriores.
- **C · Financiación anómala.** La tasa de financiación liquidada, normalizada por intervalo, alcanza el percentil 95 si es positiva o queda por debajo del percentil 5 si es negativa.

El radar del catálogo exige A y E sobre velas cerradas de cinco minutos. Hay 24 horas entre alertas de un mismo activo. Se conservan las alertas anteriores con OB y C.

El umbral de resultado predeterminado es 20%. También puedes elegir 10%, 30% o 50%. Se guarda un umbral por alerta. Tres velas consecutivas de cinco minutos con operaciones reales deben cerrar en el umbral o más allá en la misma dirección. Las ventanas abiertas y los huecos de datos se muestran por separado.

## Datos y pruebas

El escaneo general procesa hasta 24 activos por lote y continúa desde la posición guardada cerca de un minuto después de cada lote. El primer escaneo intenta recuperar los últimos 32 días de datos al contado. Las alertas, los ajustes, la caché y los registros quedan en `data/alt_event_monitor/`. Puedes exportar alertas a CSV. La pestaña de repeticiones muestra casos cuando se añade un archivo de investigación local.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor tests.test_alt_opportunities tests.test_chain_replay -v
```

## Licencia

[MIT](LICENSE)

## Donaciones

Si esta herramienta te resulta útil, puedes apoyarla con una donación voluntaria. Todas las funciones son gratuitas.

| Red | Dirección para donaciones |
| --- | --- |
| Ethereum | `0xEbc48077968d7Afda810603194b0412C35e3B655` |
| Bitcoin | `bc1qjj9dx0fwmzevwjffa55z6wp76egaztjee8ml08` |
| Solana | `2y11NNPRiJpSkLHR4zW23M67KuqxmpdTmhpKim46WtBn` |
| Tron | `TBUzeZCAuqEFuh1rUGmCnmGQoiCKkgHk4Z` |
| Stellar | `GDA4A4GN5WCMADT3GXXBKUEVIUQQPOPDVRX5B23TOVK2AGRV5LBHV545` |

Antes de enviar fondos, comprueba que tu cartera usa la red indicada junto a la dirección.
