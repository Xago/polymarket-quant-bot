# Controles de riesgo — Bot de market-making en Polymarket

Etapa 1 del proyecto. Este documento define las reglas antes de escribir
código ejecutable. Nada aquí se implementa hasta que quede validado.

Stack decidido: Python + `py-clob-client` (SDK oficial del CLOB de Polymarket).

---

## 1. Principio rector

Ninguna orden real se envía sin pasar, en orden, por: límite de posición →
límite de exposición → circuit breaker → modo dry-run/live. Si un check
falla, la orden se descarta y se loguea. No hay "override silencioso".

---

## 2. Position sizing (tamaño por operación)

- Tamaño máximo por operación: **% fijo del bankroll actual**, no monto fijo
  en USD (para que se ajuste solo si el capital crece o se reduce).
  - Default propuesto: 1-2% del bankroll por operación individual.
- Nunca calcular el tamaño sobre el bankroll inicial: siempre sobre el
  bankroll actual (evita apalancamiento fantasma tras pérdidas).
- Redondeo de tamaño siempre hacia abajo, nunca hacia arriba.

## 3. Límites de exposición (agregados)

- Exposición máxima simultánea por mercado individual: ej. 5% del bankroll.
- Exposición máxima por categoría correlacionada (ej. todos los contratos
  Up/Down de BTC): ej. 15% del bankroll. Esto es distinto de "por mercado"
  porque los contratos cripto Up/Down a menudo se mueven juntos.
- Número máximo de posiciones abiertas simultáneas (evita que el bot se
  disperse en decenas de mercados a la vez sin control real).
- Exposición total máxima del bot: % del bankroll total, el resto queda
  reservado y no tocado por el algoritmo.

## 4. Circuit breakers (frenos automáticos)

Condiciones que detienen el bot por completo hasta revisión manual:

- **Drawdown diario**: si la pérdida acumulada del día supera X% del
  bankroll → detener todas las operaciones nuevas por el resto del día.
- **Drawdown de sesión/semana**: umbral más alto que el diario, para cortar
  antes de que un mal período se vuelva catastrófico.
- **Racha de pérdidas consecutivas**: N pérdidas seguidas → pausa y
  requiere confirmación manual para reanudar (puede indicar que el régimen
  de mercado cambió y la estrategia ya no aplica).
- **Datos podridos**: si el feed de precios está stale (sin update en más
  de N segundos) o la API responde con errores repetidos → detener, no
  operar a ciegas.
- **Slippage anómalo**: si el precio de ejecución se desvía más de X% del
  precio esperado → abortar la orden, no perseguir el precio.

Cada circuit breaker es una función pura: `(estado_actual) -> bool`, testeable
sin conexión a Polymarket.

El estado que alimenta drawdown/racha (bankroll, pico, resultados recientes)
se persiste en disco (`src/bankroll_state.py`) para que tenga memoria real
entre corridas — sin esto, un proceso nuevo en cada corrida nunca podría
acumular una racha real. Validado en vivo el 2026-08-05: el freno de racha
se disparó de verdad tras 6 timeouts seguidos y bloqueó toda entrada nueva,
sin auto-resolverse (queda en punto muerto hasta reanudación manual, por
diseño). La reanudación es `bankroll_state.resume_after_breaker()`: limpia
la racha y deja un registro `RESUME` auditable en el log — nunca se llama
sola desde el loop.

### Criterios de reanudación (antes de llamar `resume_after_breaker()`)

1. **Que la racha no venga de un bug ya conocido**: confirmar que todos los
   cierres que forman la racha son posteriores al último arreglo relevante
   del código. Si alguno es de antes, no cuenta como señal real.
2. **Chequeo de plausibilidad estadística**: con la tasa de acierto
   reciente, ¿qué tan rara es esta racha por puro azar? Si la probabilidad
   es muy baja (ej. menor a 1%), tratarla como posible cambio de régimen y
   no reanudar sin investigar más. Recalcular siempre con el win rate del
   momento, no asumir un número fijo.
3. **Revisar si los mercados de la racha son representativos**: si los
   timeouts se concentraron en mercados inusualmente ilíquidos o distintos
   a los habituales, la racha dice más sobre esos mercados puntuales que
   sobre la estrategia en general.
4. **No reanudar en cadena sin pausa**: si al reanudar se dispara de nuevo
   en pocos ciclos, no reanudar automáticamente una segunda vez — ahí para
   y revisá el diseño, no solo la racha.

Validado en vivo el 2026-08-05: racha de 6 timeouts entre 07:49 y 12:30,
todos posteriores al arreglo de persistencia del bankroll (criterio 1 ✓),
con ~4,5% de probabilidad de ser puro azar (criterio 2, zona de ruido
plausible).

## 5. Kill switch manual

- Un solo comando/flag que detiene el bot de inmediato y cancela órdenes
  abiertas, sin importar el estado interno.
- Debe funcionar incluso si el bot está en un estado inconsistente (no
  depende de que el loop principal esté "sano").
- Se prueba explícitamente en Etapa 2 antes de pasar a Etapa 3.

## 6. Toma de ganancias

- Take-profit por posición: cerrar cuando el spread capturado alcanza el
  objetivo, no esperar "a ver si sigue subiendo" (la estrategia es de
  muchos spreads chicos, no de home runs).
- Barrido de ganancias (profit sweep): al superar un umbral de ganancia
  acumulada, una fracción se retira del capital operable (no se reinvierte
  todo). Reduce el riesgo de devolver todo lo ganado en una mala racha.

## 7. Modo dry-run / live (gate obligatorio)

- Flag explícito `EXECUTION_MODE=dry_run | live`, default siempre `dry_run`.
- En `dry_run`: se simulan fills contra el order book real, no se firman
  ni envían órdenes. El cierre (ganó, perdió o venció el timeout sin
  cerrar) se decide con el historial público de precio real
  (`src/position_tracker.py`), no con un resultado inventado.
- Pasar a `live` requiere: (a) todas las pruebas de Etapa 2 verdes, (b)
  confirmación explícita tuya, (c) límite de gasto máximo configurado para
  la primera sesión en vivo.

## 8. Secretos y acceso

- Claves privadas y API keys de Polymarket: solo en variables de entorno,
  nunca en código ni en logs.
- Alcance de la API en modo dry-run: solo lectura (order book, precios).
- Ninguna clave privada pasa por mis manos ni se pega en el chat.

## 9. Auditoría

- Cada decisión (entrar, salir, rechazar por circuit breaker) se loguea con
  timestamp, razón y estado del bankroll antes/después.
- Log append-only, nunca se sobreescribe ni se limpia automáticamente.

## 10. Costo real de comisión

- Polymarket cobra solo al taker (quien cruza el spread), no al maker
  (`fee = feeRate * precio * (1-precio)`, `feeRate` varía por mercado y
  viene en `feeSchedule.rate` de la API pública, ver `src/real_data_test.py`).
- El sizing usa siempre el escenario más conservador: neto = spread - fee,
  asumiendo que al menos una pata se ejecuta como taker. Si el neto es ≤ 0,
  tamaño de posición = 0, sin excepción.
- Validado con datos reales (2026-08-03): en 4 de 12 mercados activos
  probados, la comisión real se comía todo el margen del spread.

---

## Próximo paso (Etapa 2)

Con este documento validado, se construye el bot en modo `dry_run`:
implementa las reglas de arriba como funciones puras + pruebas funcionales
(`assert`-based, sin frameworks) que confirmen que cada circuit breaker
efectivamente frena el sistema en el escenario que debe frenar.

No se avanza a Etapa 3 (capital real) hasta que Etapa 2 esté validada por ti.
