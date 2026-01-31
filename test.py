from database import get_available_tests, get_current_arc_day, get_tests_for_week

test_user_id = 999999
arc_id = 1

print("🧪 Тестирование новой логики доступности тестов...")

current_day = get_current_arc_day(test_user_id, arc_id)
day_number = current_day.get('day_number', 0)

print(f"📅 Текущий день: {day_number}")

# Проверяем доступность для каждого теста
test_ranges = {
    1: (1, 7),
    2: (8, 14),
    3: (15, 21),
    4: (22, 28)
}

print("\n🔍 Проверка диапазонов:")
for week, (start, end) in test_ranges.items():
    is_available = start <= day_number <= end
    print(f"  Неделя {week} (дни {start}-{end}): {'✅ ДОСТУПЕН' if is_available else '❌ не доступен'}")

# Проверяем функцию get_available_tests
available_tests = get_available_tests(test_user_id, arc_id)
print(f"\n📋 Доступные тесты (из функции):")
if available_tests:
    for test in available_tests:
        print(f"  Неделя {test['week_num']}: {test['status']}")
else:
    print("  Нет доступных тестов")

# Проверяем вопросы
if available_tests and not available_tests[0]['completed']:
    week_num = available_tests[0]['week_num']
    questions = get_tests_for_week(week_num)
    print(f"\n📝 Вопросов для недели {week_num}: {len(questions)}")
    if questions and len(questions) >= 15:
        print("✅ Тест готов к прохождению!")
