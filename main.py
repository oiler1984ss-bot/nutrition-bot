import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, FSInputFile, BotCommand
from aiogram.utils.keyboard import InlineKeyboardBuilder
import sqlite3
from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Инициализация Groq клиента
client = AsyncGroq(api_key=GROQ_API_KEY)
MODEL_NAME = "llama-3.3-70b-versatile"  # Мощная бесплатная модель от Groq

BASE_DIR = Path(__file__).parent
IMAGES_DIR = BASE_DIR / "images"

IMG_START = str(IMAGES_DIR / "start.png")
IMG_ANKETA = str(IMAGES_DIR / "anketa.jpeg")
IMG_CALC = str(IMAGES_DIR / "calc.jpeg")
IMG_MENU = str(IMAGES_DIR / "menu.png")

ACTIVITY_COEFFICIENTS = {"minimal": 1.2, "weak": 1.375, "moderate": 1.55, "heavy": 1.725, "extreme": 1.9}

ACTIVITY_DESCRIPTIONS = {
    "minimal": ("🪑 **Минимальная активность**\n\nСидячая работа, не требующая значительных физических нагрузок.\nВы проводите большую часть дня сидя."),
    "weak": ("🚶 **Слабый уровень активности**\n\nИнтенсивные упражнения не менее 20 минут 1-3 раза в неделю.\nЭто может быть езда на велосипеде, бег трусцой, баскетбол, плавание.\nЕсли вы не тренируетесь регулярно, но сохраняете занятый образ жизни."),
    "moderate": ("🏃 **Умеренный уровень активности**\n\nИнтенсивная тренировка не менее 30-60 минут 3-4 раза в неделю.\nЛюбой из перечисленных выше видов спорта на регулярной основе."),
    "heavy": ("🏋️ **Тяжелая или трудоемкая активность**\n\nИнтенсивные упражнения и занятия спортом 5-7 дней в неделю.\nТрудоемкие занятия: строительные работы, кирпичная кладка, столярное дело, занятость в сельском хозяйстве."),
    "extreme": ("⚡ **Экстремальный уровень**\n\nЧрезвычайно активные и очень энергозатратные виды деятельности.\nЗанятия спортом с почти ежедневным графиком и несколькими тренировками в день.\nОчень трудоемкая работа: например, сгребание угля или длительный рабочий день на сборочной линии.")
}

ACTIVITY_NAMES = {"minimal": "Минимальная активность", "weak": "Слабый уровень активности", "moderate": "Умеренный уровень активности", "heavy": "Тяжелая активность", "extreme": "Экстремальный уровень"}

DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

def get_persistent_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Старт", callback_data="start_persistent")
    kb.button(text="🍽 Меню", callback_data="menu")
    kb.button(text="💬 Вопрос", callback_data="ask_question")
    kb.button(text="📊 Данные", callback_data="data")
    kb.adjust(2)
    return kb.as_markup()

class Database:
    def __init__(self, db_file="nutrition_bot.db"):
        self.conn = sqlite3.connect(db_file)
        self.cursor = self.conn.cursor()
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, gender TEXT, age INTEGER, height INTEGER, weight REAL, activity_level TEXT, activity_coefficient REAL, target TEXT, daily_calories REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS menus (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, full_menu TEXT, monday TEXT, tuesday TEXT, wednesday TEXT, thursday TEXT, friday TEXT, saturday TEXT, sunday TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        self.conn.commit()
    
    def save_user(self, user_id, username, **kwargs):
        fields = ", ".join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values())
        placeholders = ", ".join(["?" for _ in kwargs])
        self.cursor.execute(f"INSERT OR REPLACE INTO users (user_id, username, {', '.join(kwargs.keys())}) VALUES (?, ?, {placeholders})", [user_id, username] + values)
        self.conn.commit()
    
    def get_user(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if row:
            cols = [d[0] for d in self.cursor.description]
            return dict(zip(cols, row))
        return None
    
    def save_menu(self, user_id, full_menu, days_dict):
        self.cursor.execute("INSERT INTO menus (user_id, full_menu, monday, tuesday, wednesday, thursday, friday, saturday, sunday) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (user_id, full_menu, days_dict.get("Понедельник",""), days_dict.get("Вторник",""), days_dict.get("Среда",""), days_dict.get("Четверг",""), days_dict.get("Пятница",""), days_dict.get("Суббота",""), days_dict.get("Воскресенье","")))
        self.conn.commit()
    
    def get_day_menu(self, user_id, day):
        col_map = {"Понедельник":"monday", "Вторник":"tuesday", "Среда":"wednesday", "Четверг":"thursday", "Пятница":"friday", "Суббота":"saturday", "Воскресенье":"sunday"}
        col = col_map.get(day, day.lower())
        self.cursor.execute(f"SELECT {col} FROM menus WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,))
        row = self.cursor.fetchone()
        return row[0] if row and row[0] else None

class Form(StatesGroup):
    gender = State()
    age = State()
    weight = State()
    height = State()
    activity = State()
    target = State()

def calc_calories(gender, age, height, weight, coeff, target):
    bmr = 10*weight + 6.25*height - 5*age + (5 if gender=="Мужской" else -161)
    calories = bmr * coeff
    if target == "Похудеть": calories *= 0.85
    elif target == "Набрать": calories *= 1.15
    return round(calories)

async def ask_llm(prompt, max_tokens=6000):
    """Отправка запроса к Llama через Groq"""
    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Groq API error: {e}")
        raise

async def generate_menu(data):
    weight = data['weight']
    protein = round(weight * 2)
    fat = round(weight * 1)
    carbs = round((data['calories'] - protein*4 - fat*9) / 4)
    fiber = round(weight * 0.35)
    water_min = round(weight * 0.03, 1)
    water_max = round(weight * 0.04, 1)
    
    prompt = f"""Ты профессиональный нутрициолог с 15-летним опытом. Составь ДЕТАЛЬНОЕ персонализированное меню на 7 дней.

ПАРАМЕТРЫ КЛИЕНТА:
- Пол: {data['gender']}, Возраст: {data['age']} лет
- Вес: {weight} кг, Рост: {data['height']} см
- Цель: {data['target']}, Уровень активности: {data.get('activity', 'unknown')}
- Дневная норма: {data['calories']} ккал
- Белки: {protein}г, Жиры: {fat}г, Углеводы: {carbs}г, Клетчатка: {fiber}г
- Вода: {water_min}-{water_max} л/день

СТРУКТУРА КАЖДОГО ДНЯ (строго следуй ей):

=== ПОНЕДЕЛЬНИК ===

ЗАВТРАК (08:00):
• Блюдо: [конкретное название]
• Ингредиенты: [с точными граммовками]
• КБЖУ: [ккал] (Б: [г], Ж: [г], У: [г], Клетчатка: [г])

ПЕРЕКУС 1 (11:00):
• Продукты: [с граммовками]
• КБЖУ: [ккал] (Б: [г], Ж: [г], У: [г], Клетчатка: [г])

ОБЕД (14:00):
• Блюдо: [конкретное название]
• Ингредиенты: [с точными граммовками]
• КБЖУ: [ккал] (Б: [г], Ж: [г], У: [г], Клетчатка: [г])

ПЕРЕКУС 2 (17:00):
• Продукты: [с граммовками]
• КБЖУ: [ккал] (Б: [г], Ж: [г], У: [г], Клетчатка: [г])

УЖИН (20:00):
• Блюдо: [конкретное название]
• Ингредиенты: [с точными граммовками]
• КБЖУ: [ккал] (Б: [г], Ж: [г], У: [г], Клетчатка: [г])

ПОЧЕМУ ЭТО РАБОТАЕТ:
[2-3 предложения о пользе продуктов для конкретной цели клиента]

ИТОГ ЗА ДЕНЬ:
Всего: [ккал] ккал | Белки: [г]г | Жиры: [г]г | Углеводы: [г]г | Клетчатка: [г]г
💧 Вода: {water_min}-{water_max} л

[ПОВТОРИ ЭТУ СТРУКТУРУ для ВТОРНИК, СРЕДА, ЧЕТВЕРГ, ПЯТНИЦА, СУББОТА, ВОСКРЕСЕНЬЕ]

ВАЖНО:
1. Создай меню для ВСЕХ 7 дней без исключений
2. Используй реальные, доступные продукты
3. Пиши конкретные граммовки
4. Обязательно указывай клетчатку
5. Объяснения должны быть профессиональными, но понятными
6. НЕ используй символы # * _ ` (чтобы не ломать форматирование Telegram)"""

    try:
        full_text = await ask_llm(prompt, max_tokens=8000)
        
        # Парсинг дней
        days_menu = {}
        current_day = None
        current_text = []
        lines = full_text.split('\n')
        
        day_pattern = re.compile(r'(?:===\s*)?(ПОНЕДЕЛЬНИК|ВТОРНИК|СРЕДА|ЧЕТВЕРГ|ПЯТНИЦА|СУББОТА|ВОСКРЕСЕНЬЕ|ВОСКРЕСЕНИЕ)(?:\s*===)?', re.IGNORECASE)
        day_name_map = {
            'понедельник': 'Понедельник', 'вторник': 'Вторник', 'среда': 'Среда',
            'четверг': 'Четверг', 'пятница': 'Пятница', 'суббота': 'Суббота',
            'воскресенье': 'Воскресенье', 'воскресение': 'Воскресенье'
        }
        
        for line in lines:
            match = day_pattern.search(line)
            if match:
                day_found = day_name_map.get(match.group(1).lower())
                if day_found:
                    if current_day and current_text:
                        days_menu[current_day] = '\n'.join(current_text).strip()
                    current_day = day_found
                    current_text = [line]
            elif current_day:
                current_text.append(line)
        
        if current_day and current_text:
            days_menu[current_day] = '\n'.join(current_text).strip()
        
        missing_days = [d for d in DAYS if d not in days_menu]
        if missing_days:
            raise RuntimeError(f"Не сгенерировано: {', '.join(missing_days)}")
        
        final_menu = {day: days_menu[day] for day in DAYS if day in days_menu}
        logging.info(f"✅ Сгенерировано дней: {list(final_menu.keys())}")
        return full_text, final_menu
        
    except Exception as e:
        logging.error(f"❌ API Error: {e}")
        raise RuntimeError("Ошибка генерации меню")

async def ask_nutritionist(user_data, question):
    prompt = f"""Ты персональный ИИ-нутрициолог с дружелюбным и профессиональным подходом.

ДАННЫЕ КЛИЕНТА:
- Пол: {user_data['gender']}, Возраст: {user_data['age']} лет
- Вес: {user_data['weight']} кг
- Цель: {user_data['target']}
- Дневная норма: {user_data['daily_calories']} ккал

ВОПРОС КЛИЕНТА: "{question}"

ИНСТРУКЦИИ:
1. Отвечай ДРУЖЕЛЮБНО, но ПРОФЕССИОНАЛЬНО
2. Давай РАЗВЁРНУТЫЕ ответы (5-10 предложений)
3. Объясняй ПОЧЕМУ именно такие рекомендации
4. Приводи КОНКРЕТНЫЕ примеры продуктов с КБЖУ если уместно
5. МОТИВИРУЙ и поддерживай клиента
6. Не меняй его план питания без необходимости
7. НЕ используй символы # * _ `"""

    try:
        return await ask_llm(prompt, max_tokens=2000)
    except Exception as e:
        logging.error(f"❌ Chat error: {e}")
        return "Произошла ошибка. Попробуйте позже."

async def safe_send_photo(msg, photo_path, caption, reply_markup=None):
    try:
        photo = FSInputFile(photo_path)
        await msg.answer_photo(photo=photo, caption=caption, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.warning(f"⚠️ Photo error: {e}")
        await msg.answer(caption, reply_markup=reply_markup, parse_mode="Markdown")

router = Router()
db = Database()

@router.message(Command("start"))
async def start(msg: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Заполнить анкету", callback_data="survey")
    kb.button(text="🍽 Моё меню", callback_data="menu")
    kb.adjust(2)
    caption = "🌿 **Привет, друг!**\n\n**Добро пожаловать в бота-нутрициолога \"Осознанный Кусь\"!**\n\n**Здесь ты получишь:**\n✨ Индивидуальный расчет калорий\n✨ Персональное меню на неделю от ИИ-помощника\n✨ Ответы на любые вопросы по питанию\n\n**Вот главное МЕНЮ:**\n\n1️⃣ Предоставить свои данные по организму и получить расчет калорийности\n2️⃣ Сгенерировать меню с помощью ИИ\n\nВыберите действие:"
    await safe_send_photo(msg, IMG_START, caption, kb.as_markup())

@router.callback_query(F.data == "start_persistent")
async def start_persistent(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Заполнить анкету", callback_data="survey")
    kb.button(text="🍽 Моё меню", callback_data="menu")
    kb.adjust(2)
    caption = "🌿 **Привет!**\n\n**Бот-нутрициолог \"Осознанный Кусь\"**\n\nВыберите действие:"
    await safe_send_photo(cb.message, IMG_START, caption, kb.as_markup())

@router.callback_query(F.data == "survey")
async def survey_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_send_photo(cb.message, IMG_ANKETA, "📋 **Заполнение анкеты**\n\nЭто займет всего 2 минуты!\n\nМы зададим несколько вопросов о вас, чтобы создать персонализированный план питания.\n\n**Шаг 1 из 6:** Выберите ваш пол:", InlineKeyboardBuilder().button(text="👨 Мужской", callback_data="m").button(text="👩 Женский", callback_data="f").adjust(2).as_markup())
    await state.set_state(Form.gender)

@router.callback_query(Form.gender, F.data.in_(["m", "f"]))
async def set_gender(cb: CallbackQuery, state: FSMContext):
    gender = "Мужской" if cb.data=="m" else "Женский"
    emoji = "👨" if cb.data=="m" else "👩"
    await state.update_data(gender=gender)
    await cb.message.answer(f"✅ {emoji} **Пол: {gender}**\n\n**Шаг 2 из 6:** Укажите, сколько вам полных лет\n*(Например: 25)*", parse_mode="Markdown")
    await state.set_state(Form.age)

@router.message(Form.age)
async def set_age(msg: Message, state: FSMContext):
    if not msg.text.isdigit() or not (10 <= int(msg.text) <= 100):
        await msg.answer("❌ Пожалуйста, укажите возраст от 10 до 100 лет")
        return
    age = int(msg.text)
    await state.update_data(age=age)
    await msg.answer(f"✅ **Возраст: {age} лет**\n\n**Шаг 3 из 6:** Укажите ваш вес в килограммах\n*(Например: 70 или 70.5)*", parse_mode="Markdown")
    await state.set_state(Form.weight)

@router.message(Form.weight)
async def set_weight(msg: Message, state: FSMContext):
    try:
        w = float(msg.text.replace(",", "."))
        if not (30 <= w <= 300):
            raise ValueError
    except:
        await msg.answer("❌ Пожалуйста, укажите вес от 30 до 300 кг")
        return
    await state.update_data(weight=w)
    await msg.answer(f"✅ **Вес: {w} кг**\n\n**Шаг 4 из 6:** Укажите ваш рост в сантиметрах\n*(Например: 175)*", parse_mode="Markdown")
    await state.set_state(Form.height)

@router.message(Form.height)
async def set_height(msg: Message, state: FSMContext):
    if not msg.text.isdigit() or not (100 <= int(msg.text) <= 250):
        await msg.answer("❌ Пожалуйста, укажите рост от 100 до 250 см")
        return
    await state.update_data(height=int(msg.text))
    kb = InlineKeyboardBuilder()
    kb.button(text="🪑 Минимальная", callback_data="minimal")
    kb.button(text="🚶 Слабый", callback_data="weak")
    kb.button(text="🏃 Умеренный", callback_data="moderate")
    kb.button(text="🏋️ Тяжелая", callback_data="heavy")
    kb.button(text="⚡ Экстремальный", callback_data="extreme")
    kb.adjust(1)
    descriptions_text = f"✅ **Рост: {msg.text} см**\n\n**Шаг 5 из 6:** Укажите ваш уровень физической активности\n\n**Выберите подходящий вариант:**\n\n"
    for key in ["minimal", "weak", "moderate", "heavy", "extreme"]:
        descriptions_text += ACTIVITY_DESCRIPTIONS[key] + "\n\n"
    descriptions_text += "---\n\nНажмите на кнопку, которая лучше всего описывает ваш образ жизни:"
    await msg.answer(descriptions_text, reply_markup=kb.as_markup(), parse_mode="Markdown")
    await state.set_state(Form.activity)

@router.callback_query(Form.activity, F.data.in_(list(ACTIVITY_COEFFICIENTS.keys())))
async def set_activity(cb: CallbackQuery, state: FSMContext):
    await state.update_data(activity=ACTIVITY_NAMES[cb.data], activity_key=cb.data, coeff=ACTIVITY_COEFFICIENTS[cb.data])
    kb = InlineKeyboardBuilder()
    kb.button(text="🎯 Поддерживать форму", callback_data="maintain")
    kb.button(text="📉 Похудеть", callback_data="lose")
    kb.button(text="📈 Набрать массу", callback_data="gain")
    kb.adjust(1)
    await cb.message.answer(f"✅ **{ACTIVITY_NAMES[cb.data]}**\n\n**Шаг 6 из 6:** Выберите вашу цель:", reply_markup=kb.as_markup(), parse_mode="Markdown")
    await state.set_state(Form.target)

@router.callback_query(Form.target, F.data.in_(["maintain", "lose", "gain"]))
async def set_target(cb: CallbackQuery, state: FSMContext):
    target_map = {"maintain": "Поддерживать форму", "lose": "Похудеть", "gain": "Набрать массу"}
    await state.update_data(target=target_map[cb.data])
    data = await state.get_data()
    calories = calc_calories(data['gender'], data['age'], data['height'], data['weight'], data['coeff'], data['target'])
    await state.update_data(calories=calories)
    db.save_user(cb.from_user.id, cb.from_user.username or "user", gender=data['gender'], age=data['age'], height=data['height'], weight=data['weight'], activity_level=data['activity'], activity_coefficient=data['coeff'], target=data['target'], daily_calories=calories)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, сгенерировать", callback_data="gen")
    kb.button(text="❌ Отмена", callback_data="cancel")
    kb.adjust(2)
    await safe_send_photo(cb.message, IMG_CALC, f"🎉 **Анкета заполнена!**\n\n✅ **Ваша дневная норма калорий:** {calories} ккал/день\n\n📊 **Расчет произведен по формуле Миффлина-Сан Жеора**\nс учетом вашего уровня активности и цели.\n\n🍽 **Сгенерировать персональное меню на 7 дней?**\n\nИИ создаст подробный план питания с:\n• Точными граммовками\n• Расчетом БЖУ+клетчатка для каждого приема пищи\n• Объяснениями почему это работает\n• Разнообразными блюдами на каждый день", kb.as_markup())

@router.callback_query(F.data == "gen")
async def generate(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await cb.message.answer("🔄 **Генерирую персональное меню на 7 дней...**\n\n⏱ Это займет 60-90 секунд\n\n🤖 ИИ анализирует ваши параметры и создает оптимальный план питания\nс учетом:\n• Вашей дневной нормы: {} ккал\n• Цели: {}\n• Уровня активности: {}\n\n☕ Пока можете сделать перерыв!".format(data['calories'], data['target'], data.get('activity', '')), parse_mode="Markdown")
    await asyncio.sleep(30)
    try:
        full, days = await generate_menu(data)
        db.save_menu(cb.from_user.id, full, days)
        kb = InlineKeyboardBuilder()
        for day in DAYS:
            kb.button(text=day, callback_data=f"day_{day}")
        kb.adjust(1)
        await safe_send_photo(cb.message, IMG_MENU, f"🎉 **Ваш план питания готов!**\n\n✅ Сгенерировано меню на **7 дней**\n📊 Дневная норма: **{data['calories']} ккал**\n\n📅 **Выберите день** для просмотра детального меню:\n\n💡 *Каждый день включает:*\n• 5 приемов пищи с точными граммовками\n• Полный расчет БЖУ+клетчатка\n• Объяснения пользы продуктов", kb.as_markup())
    except RuntimeError as e:
        logging.error(f"❌ Generation failed: {e}")
        kb = InlineKeyboardBuilder()
        kb.button(text="🏠 Старт", callback_data="start_persistent")
        kb.button(text="🍽 Меню", callback_data="menu")
        kb.adjust(2)
        await cb.message.answer("❌ **Не удалось сгенерировать меню**\n\nПопробуйте еще раз.", reply_markup=kb.as_markup(), parse_mode="Markdown")
    except Exception as e:
        logging.error(f"❌ Error: {e}")
        kb = InlineKeyboardBuilder()
        kb.button(text="🏠 Старт", callback_data="start_persistent")
        kb.button(text="🍽 Меню", callback_data="menu")
        kb.adjust(2)
        await cb.message.answer("❌ **Ошибка при генерации меню**\n\nПопробуйте позже.", reply_markup=kb.as_markup(), parse_mode="Markdown")
    await state.clear()

@router.callback_query(F.data.startswith("day_"))
async def show_day(cb: CallbackQuery):
    day = cb.data.replace("day_", "")
    menu = db.get_day_menu(cb.from_user.id, day)
    if menu:
        parts = [menu[i:i+4000] for i in range(0, len(menu), 4000)]
        for i, part in enumerate(parts):
            if i == len(parts)-1:
                await cb.message.answer(part, reply_markup=get_persistent_keyboard())
            else:
                await cb.message.answer(part)
    else:
        await cb.answer("❌ Сгенерируйте меню", show_alert=True)

@router.callback_query(F.data == "days")
async def days_list(cb: CallbackQuery):
    kb = InlineKeyboardBuilder()
    for day in DAYS:
        kb.button(text=day, callback_data=f"day_{day}")
    kb.adjust(1)
    await cb.message.answer("📅 Выберите день:", reply_markup=kb.as_markup())

@router.callback_query(F.data == "menu")
async def show_menu(cb: CallbackQuery):
    user = db.get_user(cb.from_user.id)
    if not user:
        await cb.answer("❌ Заполните анкету", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 По дням", callback_data="days")
    kb.button(text="🔄 Новое", callback_data="gen")
    kb.button(text="📊 Данные", callback_data="data")
    kb.button(text="💬 Вопрос", callback_data="ask_question")
    kb.adjust(2)
    await cb.message.answer("🍽 Меню:", reply_markup=kb.as_markup())

@router.callback_query(F.data == "data")
async def show_data(cb: CallbackQuery):
    user = db.get_user(cb.from_user.id)
    if not user:
        await cb.answer("❌ Нет данных", show_alert=True)
        return
    weight = user['weight']
    water_min = round(weight * 0.03, 1)
    water_max = round(weight * 0.04, 1)
    text = (f"📊 **Ваши данные:**\n\n👤 Пол: {user['gender']}\n🎂 Возраст: {user['age']} лет\n⚖️ Вес: {user['weight']} кг\n📏 Рост: {user['height']} см\n🏃 Активность: {user['activity_level']}\n📈 Коэффициент: {user['activity_coefficient']}\n🎯 Цель: {user['target']}\n🔥 Норма: {user['daily_calories']} ккал\n\n💧 **Рекомендации по воде:**\n• Минимум: {water_min} л/день\n• Оптимально: {water_max} л/день\n• Утром натощак: 1-2 стакана\n• За 30 мин до еды: 1 стакан")
    await cb.message.answer(text, reply_markup=get_persistent_keyboard(), parse_mode="Markdown")

@router.callback_query(F.data == "ask_question")
async def ask_question_prompt(cb: CallbackQuery):
    await cb.message.answer("💬 **Задайте вопрос нутрициологу**\n\nНапример:\n• Что можно вместо киноа?\n• Сколько воды мне нужно пить?\n• Можно ли есть фрукты вечером?\n• Чем заменить курицу?\n\nПросто напишите ваш вопрос:")
    await cb.answer()

@router.callback_query(F.data == "cancel")
async def cancel_handler(cb: CallbackQuery):
    await cb.message.answer("❌ Отменено", reply_markup=get_persistent_keyboard())

@router.message(F.text)
async def handle_chat(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        return
    user_data = db.get_user(message.from_user.id)
    if not user_data or not user_data.get("daily_calories"):
        kb = InlineKeyboardBuilder()
        kb.button(text="📋 Анкета", callback_data="survey")
        kb.button(text="🏠 Старт", callback_data="start_persistent")
        kb.adjust(2)
        await message.answer("📋 Заполните анкету!", reply_markup=kb.as_markup())
        return
    analyzing_msg = await message.answer("🤔 Анализирую ваш вопрос...")
    try:
        answer = await ask_nutritionist(user_data, message.text)
        await analyzing_msg.delete()
        parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
        for i, part in enumerate(parts):
            if i == len(parts)-1:
                await message.answer(part, reply_markup=get_persistent_keyboard())
            else:
                await message.answer(part, reply_markup=get_persistent_keyboard())
    except Exception as e:
        logging.error(f"❌ Chat error: {e}")
        await analyzing_msg.delete()
        await message.answer("❌ Произошла ошибка. Попробуйте позже.", reply_markup=get_persistent_keyboard())

async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    bot = Bot(token=BOT_TOKEN)
    await bot.set_my_commands([BotCommand(command="start", description="🏠 Главное меню"), BotCommand(command="menu", description="📋 Моё меню"), BotCommand(command="data", description="📊 Мои данные"), BotCommand(command="question", description="💬 Задать вопрос")])
    dp = Dispatcher()
    dp.include_router(router)
    print("✅ БОТ ЗАПУЩЕН!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())