# Сборка дашборда в Tableau Public

## Холст и стиль

- Размер: Fixed, 1440 × 900.
- Фон: `#F7F8FA`.
- Основной текст: `#172033`.
- Акцент: `#5B5FEF`.
- Положительное значение: `#15A36D`; риск: `#E55757`.
- Шрифт: Tableau Book / Arial. Заголовок 24 pt, KPI 26–30 pt, подписи 10–12 pt.
- Без тяжелых рамок: карточки отделяются белым фоном и внутренними отступами.

## Источники данных

Каждый CSV подключается как отдельный text-file source из `data/processed/`:

1. `tableau_model_summary.csv` — KPI действующего банка.
2. `tableau_monthly_activity.csv` — динамика базы.
3. `tableau_tenure_curve.csv` — survival, activity и накопленная маржа.
4. `tableau_break_even_scenarios.csv` — готовая sensitivity-модель.
5. `tableau_cohort_summary.csv` — сравнение когорт.
6. `tableau_client_month.csv` — детальная когортная heatmap.

Для быстрого подключения всех листов одним источником используйте
`tableau_dashboard_data.csv`. Поле `RECORD_TYPE` разделяет строки `SUMMARY`,
`TENURE`, `SCENARIO`, `COHORT` и `MONTHLY`. Именно этот источник упакован в
готовый `.twbx`.

Для дат Tableau должен определить тип Date. Денежные поля — Number (decimal), количества — Number (whole).

## Компоновка Dashboard «Окупаемость»

### Верхняя полоса — вывод

Текст: «Банк операционно прибылен: +45,0 млн руб./мес. до нового привлечения».

Четыре KPI-карточки:

- Текущая прибыль, руб./мес.
- Маржа клиента после CAC, руб.
- Требуется новых клиентов/мес.
- Наблюдаемый горизонт, мес.

Источник: `tableau_model_summary.csv`.

### Левая центральная зона — Unit economics by tenure

Источник: `tableau_tenure_curve.csv`.

- Columns: `TENURE_MONTH`.
- Rows: `ACTIVE_PROBABILITY` и `SURVIVAL_RATE` через Measure Values.
- Mark: Line.
- Цвета: active probability `#5B5FEF`, survival `#AAB1C2`.
- Tooltip: eligible, open, active accounts.
- Фильтр: `IS_RELIABLE_SAMPLE = 1`.

Рядом/под ним — линия `CUM_MARGIN_AFTER_CAC_ACTIVE_SERVICE_RUB`; нулевая reference line показывает месяц payback.

### Правая центральная зона — главный ответ на вопрос 2

Источник: `tableau_break_even_scenarios.csv`.

- Columns: `AVERAGE_BALANCE_RUB`.
- Rows: `REQUIRED_NEW_CLIENTS_MONTH`.
- Color: `COST_BASIS`.
- Mark: Line, размер 3.
- Фильтр по умолчанию: `COST_BASIS = Только активные месяцы`.
- Формат X: Display Units = Thousands, suffix ` тыс. ₽`.
- Tooltip: средний остаток, unit margin, требуемые клиенты.
- Не соединять null: в этой области юнит-экономика отрицательная.

Добавить Parameter `Выбранный средний остаток` от 0 до 1 000 000 с шагом 25 000 и calculated field:

```text
[AVERAGE_BALANCE_RUB] = [Выбранный средний остаток]
```

Его можно использовать как фильтр для отдельной KPI-карточки выбранного сценария.

### Нижняя зона — когорты

Источник: `tableau_client_month.csv`.

- Rows: `OPEN_MONTH`, discrete month.
- Columns: `TENURE_MONTH`.
- Color: AVG(`ACTIVE`).
- Mark: Square.
- Палитра: светло-серый → `#5B5FEF`, диапазон 0–1.
- Label отключить; значения показывать в tooltip как проценты.

Показать последние 12–18 когорт, чтобы heatmap оставалась читаемой.

## Подписи и оговорки

Внизу дашборда разместить текст:

> LTV рассчитан на наблюдаемом 29-месячном горизонте. Базовый сценарий: обслуживание оплачивается только в активные месяцы, доход на остатки также возникает в активные месяцы. Доступен альтернативный сценарий обслуживания всех открытых счетов.

## Story для собеседования

1. При данных вводных действующий банк уже прибылен: +45 млн руб./мес. до CAC новых клиентов.
2. Кривые показывают постепенное снижение survival и активности; расчеты корректируются на правое цензурирование через eligible accounts.
3. На нулевом остатке новый поток окупает 5 млн fixed costs примерно при 2,1 тыс. привлечений в месяц в базовом сценарии.
4. Рост среднего остатка резко снижает необходимый приток клиентов, потому что добавляет повторяющийся процентный доход к каждому активному месяцу.
