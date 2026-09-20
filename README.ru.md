# Codex Usage

[English](README.md) | **Русский**

Локальная аналитика использования OpenAI Codex и оценка эквивалентной стоимости по API-тарифам.

Codex Usage читает локальную SQLite-базу и rollout-телеметрию Codex, восстанавливает активность основных агентов и субагентов и формирует Excel-дашборд с использованием токенов, эффективностью кэша, экономикой задач и оценкой стоимости по эквивалентным API-тарифам.

Весь анализ выполняется локально. База Codex, промпты, история задач и сформированные отчёты никуда не загружаются этим инструментом.

## Возможности

- Чтение локальной SQLite-базы Codex
- Парсинг текущего и legacy-формата rollout-телеметрии
- Учёт основных агентов, субагентов и auto-review
- Восстановление деревьев задач по связям между агентами Codex
- Аналитика по дням, неделям, месяцам, проектам, моделям и задачам
- Input, cached input, uncached input, output и reasoning tokens
- Анализ эффективности prompt cache
- Экономика в разрезе Model × Reasoning Effort
- Оценка API-equivalent стоимости в USD и RUB
- Контроль pricing coverage и явный учёт токенов без известного тарифа
- Excel-дашборд с:
  - KPI текущего периода
  - ежедневным использованием
  - самыми дорогими проектами
  - самыми дорогими задачами
  - экономикой моделей и effort
  - экономикой effort основных агентов

## Как это работает

Codex Usage использует два локальных источника данных.

### 1. `~/.codex/state_5.sqlite`

Из базы извлекаются:

- threads;
- рабочие директории проектов;
- модели;
- reasoning effort;
- связи parent/child между агентами.

### 2. Rollout JSONL-файлы Codex

Из rollout-телеметрии извлекаются:

- события использования токенов;
- input tokens;
- cached input tokens;
- output tokens;
- reasoning tokens.

Далее данные проходят следующий pipeline:

```text
Codex SQLite + rollouts
        ↓
парсер usage
        ↓
иерархия threads / agents
        ↓
экономика задач
        ↓
daily / weekly / monthly аналитика
        ↓
Excel dashboard
```

## Установка

Требуется Python 3.13+.

```bash
git clone https://github.com/shelasmax/codex-usage.git
cd codex-usage

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

## Конфигурация

Создайте локальный конфиг из примера:

```bash
cp config.example.yaml config.yaml
```

Отредактируйте `config.yaml` под своё окружение.

Пример:

```yaml
timezone: "Europe/Moscow"

codex:
  database: "~/.codex/state_5.sqlite"

output:
  excel: "output/Codex_Usage.xlsx"

pricing:
  usd_rub: 80.00

project_aliases:
  "/Users/yourname/Documents/Projects/example-project": "Example Project"
```

`project_aliases` необязателен. Codex Usage автоматически обнаруживает рабочие директории (`cwd`), а aliases позволяют назначить им более понятные названия в отчёте.

## Тарифы

API-equivalent тарифы моделей хранятся в:

```text
pricing.yaml
```

Цены указаны в USD за 1 миллион токенов.

Если для модели нет известного тарифа, Codex Usage намеренно оставляет её **unpriced**, а не назначает предполагаемую стоимость.

Благодаря этому в отчёте отдельно видны:

- priced tokens;
- unpriced tokens;
- pricing coverage.

## Создание отчёта

```bash
python build_excel.py
```

Отчёт будет создан в:

```text
output/Codex_Usage.xlsx
```

В книге формируются листы:

- `Dashboard`
- `Models`
- `Daily`
- `Weekly`
- `Monthly`
- `Projects`
- `Tasks`

## Диагностические инструменты

### Проверка локальной базы и usage

```bash
python main.py
```

### Аналитика по дням

```bash
python inspect_daily.py
```

### Аналитика деревьев задач

```bash
python inspect_tasks.py
```

## Сравнение периодов использования

Можно сравнить два периода работы Codex, чтобы исследовать влияние модели, reasoning effort, orchestration агентов или причины аномально высокого расхода.

```bash
python compare_periods.py \
  --a-start "2026-01-01T09:00:00" \
  --a-end "2026-01-01T17:00:00" \
  --b-start "2026-01-08T09:00:00" \
  --b-end "2026-01-08T17:00:00" \
  --a-label "До" \
  --b-label "После"
```

Сравнение включает:

- total, input, cached, uncached, output и reasoning tokens
- API-equivalent стоимость
- ROOT vs SUBAGENT
- Model × Reasoning Effort × Role
- оценку активного времени работы
- tokens и calls на активный час
- использование по проектам
- концентрацию расхода по задачам
- количество активных child agents и fan-out
- самые ресурсоёмкие задачи с разделением direct/subagent usage

### Оценка активного времени

По умолчанию пауза более 30 минут между token events считается началом новой active session.

Порог можно изменить:

```bash
python compare_periods.py \
  ... \
  --active-gap 60
```

Active time — расчётная метрика на основе telemetry Codex. Она не означает фактическое время пользователя за компьютером.

### Для чего это полезно

Сравнение периодов позволяет исследовать:

- Medium vs High/XHigh reasoning effort
- разные конфигурации моделей
- изменение стратегии subagents
- эффект изменений orchestration
- аномально быстрое расходование allowance
- высокий agent fan-out
- слишком долгоживущие root sessions

## Методика оценки стоимости

Для модели с известным тарифом:

```text
стоимость uncached input
+ стоимость cached input
+ стоимость output
= API-equivalent cost
```

Cached input рассчитывается отдельно от uncached input.

Reasoning tokens выводятся в аналитике отдельно, но не прибавляются повторно поверх output tokens при расчёте стоимости.

### Важно

Рассчитанная стоимость — это **API-equivalent estimate**.

Это **не реконструкция биллинга подписки ChatGPT или Codex** и не сумма, которую пользователь фактически заплатил за использование Codex.

Текущие ограничения:

- long-context surcharges пока не учитываются;
- внутренние модели без известного тарифа остаются unpriced;
- отдельные tool-specific charges не учитываются;
- конвертация USD в локальную валюту использует курс, вручную заданный в конфигурации.

Поэтому Dashboard отдельно показывает:

- priced API-equivalent cost;
- pricing coverage;
- unpriced tokens.

## Конфиденциальность

Codex Usage спроектирован как локальный инструмент.

В Git не должны попадать:

- `config.yaml`;
- `.venv/`;
- сгенерированные Excel-отчёты;
- локальные базы Codex;
- rollout-файлы;
- логи.

Включённый `.gitignore` исключает локальную конфигурацию и сгенерированные отчёты.

Названия задач перед записью в Excel ограничиваются безопасной длиной. Исходные данные Codex при этом не изменяются.

## Статус проекта

Ранняя open-source версия.

Локальное хранилище Codex и форматы rollout являются деталями реализации и могут меняться между версиями Codex. При изменении форматов может потребоваться обновление парсера.

Проект протестирован через полный clean-clone smoke test:

```text
GitHub clone
→ новый Python venv
→ установка requirements
→ загрузка локальной Codex DB
→ парсинг rollout telemetry
→ построение аналитики
→ генерация XLSX
→ проверка структуры workbook
```

## Лицензия

MIT