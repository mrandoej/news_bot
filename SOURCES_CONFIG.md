# Настройка источников новостей

## Как это работает

Бот может загружать источники новостей из:
1. JSON файла `sources.json` (основной способ)
2. Переменных окружения (быстрое включение/отключение)
3. Встроенных источников (если ничего не настроено)

## Настройка через JSON файл

### Структура файла sources.json

```json
{
  "sources": [
    {
      "id": "unique_id",
      "name": "Название источника",
      "url": "https://example.com",
      "city": "Саратов",
      "rss": "https://example.com/rss.xml",
      "enabled": true,
      "priority": 1,
      "timeout": 30
    }
  ]
}
```

### Параметры источника

- `id` - уникальный идентификатор (обязательно)
- `name` - название для логов (обязательно)
- `url` - адрес сайта (обязательно)
- `city` - город (по умолчанию "Саратов")
- `rss` - адрес RSS ленты (для RSS источников)
- `selector` - CSS селектор (для HTML источников)
- `enabled` - включен ли источник (по умолчанию true)
- `priority` - приоритет 1-3 (по умолчанию 1)
- `timeout` - таймаут в секундах (по умолчанию 30)

### Типы источников

#### RSS источники (проще)
```json
{
  "id": "example_rss",
  "name": "Пример RSS",
  "url": "https://example.com",
  "rss": "https://example.com/rss.xml"
}
```

#### HTML источники (сложнее)
```json
{
  "id": "example_html",
  "name": "Пример HTML",
  "url": "https://example.com",
  "selector": "article, .news-item, h2"
}
```

## Настройка через переменные окружения

В файле `.env`:

```env
# Путь к файлу с источниками
NEWS_SOURCES_FILE=./sources.json

# Включить только эти источники
ENABLED_SOURCES=lenta_ru,tass,interfax

# Отключить эти источники
DISABLED_SOURCES=problematic_source
```

### Логика работы

1. Если указан `ENABLED_SOURCES` - включает только эти источники
2. Если указан `DISABLED_SOURCES` - отключает эти источники
3. `ENABLED_SOURCES` важнее чем `DISABLED_SOURCES`

## Примеры использования

### Включить только федеральные источники

```env
ENABLED_SOURCES=lenta_ru,ria_novosti,interfax,tass
```

### Отключить проблемные источники

```env
DISABLED_SOURCES=slow_source,broken_source
```

### Использовать другой файл конфигурации

```env
NEWS_SOURCES_FILE=./config/my_sources.json
```

## Добавление нового источника

1. Откройте `sources.json`
2. Добавьте новый объект в массив `sources`
3. Перезапустите бота

Пример:
```json
{
  "id": "new_source",
  "name": "Новый источник",
  "url": "https://newssite.com",
  "rss": "https://newssite.com/rss.xml",
  "enabled": true
}
```

## Отладка источников

### Проверить все источники

```bash
python app.py test
```

### Проверить конкретный источник

```bash
ENABLED_SOURCES=lenta_ru python app.py once
```

### Посмотреть статистику

```bash
python app.py stats
```

## Встроенные источники

Если файл `sources.json` не найден, используются:
- lenta_ru (Лента.ру)
- ria_novosti (РИА Новости)
- interfax (Интерфакс)
- tass (ТАСС)

## Советы

### Для продакшена
- Используйте JSON файл для основной настройки
- Переменные окружения для быстрых изменений
- Следите за логами на ошибки источников

### Для разработки
- Отключайте медленные источники через `DISABLED_SOURCES`
- Тестируйте новые источники по одному
- Используйте короткие таймауты для быстрой отладки

### Производительность
- Приоритет 1 для важных источников
- Приоритет 2-3 для дополнительных
- Таймаут 30 секунд для стабильных источников
- Таймаут 10 секунд для медленных

## Если источник не работает

1. Проверьте URL в браузере
2. Для RSS - убедитесь что лента доступна
3. Для HTML - проверьте CSS селекторы
4. Увеличьте timeout если сайт медленный
5. Посмотрите логи: `tail -f logs/bot.log`

## Примеры конфигураций

### Минимальная
```json
{
  "sources": [
    {
      "id": "tass",
      "name": "ТАСС",
      "url": "https://tass.ru",
      "rss": "https://tass.ru/rss/v2.xml"
    }
  ]
}
```

### Полная
```json
{
  "sources": [
    {
      "id": "local_news",
      "name": "Местные новости",
      "url": "https://local-news.ru",
      "city": "Саратов",
      "rss": "https://local-news.ru/rss.xml",
      "enabled": true,
      "priority": 1,
      "timeout": 30
    }
  ]
}
```