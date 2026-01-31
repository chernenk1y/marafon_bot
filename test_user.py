import sqlite3
from datetime import datetime, timedelta

print("🔧 Устанавливаю тестовому пользователю день в диапазоне 1-7...")

conn = sqlite3.connect('mentor_bot.db')
cursor = conn.cursor()

test_user_id = 999999

# Устанавливаем дату начала марафона так, чтобы текущий день был в диапазоне 1-7
# Для теста недели 1 нужен день 1-7
# Устанавливаем дату начала 3 дня назад (будет день 4)
days_ago = 3
purchased_at = (datetime.now() - timedelta(days=days_ago)).isoformat()

# Обновляем дату покупки доступа
cursor.execute('''
    UPDATE user_arc_access 
    SET purchased_at = ?
    WHERE user_id = ? AND arc_id = 1
''', (purchased_at, test_user_id))

# Обновляем дату начала марафона
arc_start_date = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
cursor.execute('''
    UPDATE arcs 
    SET дата_начала = ?
    WHERE arc_id = 1
''', (arc_start_date,))

conn.commit()

# Проверяем
from database import get_current_arc_day
current_day = get_current_arc_day(test_user_id, 1)
day_number = current_day.get('day_number', 0)

print(f"✅ Установлена дата начала марафона: {arc_start_date}")
print(f"✅ Текущий день тестового пользователя: {day_number}")

# Проверяем доступность тестов
if 1 <= day_number <= 7:
    print("✅ Тест недели 1 ДОСТУПЕН (дни 1-7)")
else:
    print("❌ Тест недели 1 НЕ доступен")

conn.close()

print("\n🎯 Теперь тест недели 1 должен быть доступен!")