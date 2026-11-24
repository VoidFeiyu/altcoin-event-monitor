# Análisis de oportunidades en altcoins

[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · Español

Herramienta de investigación para Windows con alertas de altcoins, financiación y base, diferenciales en cadena y simulación de cobertura LP. El radar sigue RAVE, BSB, EVAA y LAB y registra los resultados de las alertas a 3, 7 y 14 días. Usa datos públicos de solo lectura, sin cuenta ni clave de API de trading.

## Instalación y uso

Descarga el código e instala Python 3.12 con Tcl/Tk. Abre PowerShell desde el directorio del proyecto y ejecuta estos comandos.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

Después de la instalación también puedes abrir `start-monitor.bat`. Selecciona Español en el menú de idioma para guardar tu preferencia. Pulsa **Iniciar monitor** para activar los escaneos periódicos o **Escanear ahora** para hacer solo uno. Al cerrar la ventana se detiene el monitor.

## Análisis de oportunidades

- **Financiación y base.** Compara ETH, SOL y LINK al contado y en perpetuos lineales de OKX. Calcula el neto con cantidades iguales, intervalos reales y comisiones de ida y vuelta. La primera estimación positiva se sigue con la tasa liquidada y el libro posterior.
- **Diferenciales en cadena.** Compara ambos sentidos para ETH y LINK entre Uniswap v3 de Ethereum y el mercado USDC de OKX. Supone inventario en ambos mercados y descuenta comisiones y gas estimado. La duración positiva se basa en muestras válidas consecutivas.
- **Simulación de cobertura LP.** Usa el historial del pool ETH/USDC al 0,05% de Uniswap v3 para comparar el LP con y sin cobertura mediante el perpetuo ETH de OKX. Permite configurar unas 6, 12 o 24 horas, capital, rango, cobertura y costes. Valora USDT y USDC 1:1.

Las dos primeras páginas admiten muestras puntuales o cada cinco minutos. Cada página permite exportar la evidencia seleccionada a JSON. Las fuentes públicas son OKX, Ethereum dRPC y PublicNode. Los resultados son simulados y excluyen el capital de margen y el coste de mover inventario entre mercados.

## Reglas de alerta

- **A · Pico de volumen al contado.** El volumen cotizado de la última hora alcanza el doble de la mediana horaria y el percentil 95 de los 30 días anteriores.
- **E · Variación de precio.** El cambio absoluto del precio al contado en la última hora alcanza el percentil 95 de los 30 días anteriores.
- **OB · Pico de interés abierto.** El crecimiento de la cantidad de interés abierto en cuatro horas alcanza el 10% y el percentil 95 de los 30 días anteriores.
- **C · Financiación anómala.** La tasa de financiación liquidada, normalizada por intervalo, alcanza el percentil 95 si es positiva o queda por debajo del percentil 5 si es negativa.

El modelo al contado exige A y E. El completo exige al menos dos condiciones, incluida A u OB. Hay 24 horas de espera entre alertas del mismo token y modelo. El modelo al contado puede funcionar aunque falten datos de derivados.

El umbral de resultado predeterminado es 20%. También puedes elegir 10%, 30% o 50%. Se guarda un umbral por alerta. Tres velas consecutivas de cinco minutos con operaciones reales deben cerrar en el umbral o más allá en la misma dirección. Las ventanas abiertas y los huecos de datos se muestran por separado.

## Datos y pruebas

El primer escaneo intenta recuperar los últimos 32 días de datos al contado. Las alertas, los ajustes, la caché y los registros quedan en `data/alt_event_monitor/`. Puedes exportar alertas a CSV. La pestaña de repeticiones muestra casos cuando se añade un archivo de investigación local.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor tests.test_alt_opportunities -v
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
