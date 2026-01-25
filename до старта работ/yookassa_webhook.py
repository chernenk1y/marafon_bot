#!/usr/bin/env python3
"""
Webhook обработчик для Юкассы
Запускается отдельно на сервере
"""

from flask import Flask, request, jsonify
import sqlite3
import json
from datetime import datetime

app = Flask(__name__)

# Импортируем функции из database
import sys
sys.path.append('.')
from database import update_payment_status

@app.route('/yookassa_webhook', methods=['POST'])
def webhook_handler():
    """Обрабатывает webhook от Юкассы"""
    try:
        data = request.json
        print(f"📥 Получен webhook: {json.dumps(data, ensure_ascii=False)}")
        
        event = data.get('event')
        payment = data.get('object', {})
        
        if event == 'payment.succeeded':
            payment_id = payment.get('id')
            status = payment.get('status')
            
            if payment_id and status:
                update_payment_status(payment_id, status)
                print(f"✅ Платеж {payment_id} успешно обработан")
                
                # Можно отправить уведомление в Telegram
                # (реализуем позже)
                
                return jsonify({'status': 'success'}), 200
            else:
                print("🚨 Некорректные данные платежа")
                return jsonify({'error': 'Invalid payment data'}), 400
                
        elif event == 'payment.canceled':
            payment_id = payment.get('id')
            update_payment_status(payment_id, 'canceled')
            print(f"✅ Платеж {payment_id} отменен")
            return jsonify({'status': 'success'}), 200
            
        else:
            print(f"⚠️ Неизвестное событие: {event}")
            return jsonify({'status': 'ignored'}), 200
            
    except Exception as e:
        print(f"🚨 Ошибка обработки webhook: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Проверка работоспособности"""
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

if __name__ == '__main__':
    # Важно: настройте SSL для production!
    app.run(host='0.0.0.0', port=5000, debug=True)