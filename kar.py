import asyncio
import threading
import os
import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk
import httpx
from playwright.async_api import async_playwright

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- НАСТРОЙКИ БЕЗОПАСНОСТИ И КОНФИГУРАЦИИ ---
TG_TOKEN = os.getenv("TG_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1"))

# --- НАСТРОЙКИ ИГРЫ ---
GAME_URL = os.getenv("GAME_URL", "https://quarantine-game.ru/")
NUM_ACCOUNTS = int(os.getenv("NUM_ACCOUNTS", "4"))
BASE_PROFILES_PATH = os.path.abspath(os.getenv("PROFILES_PATH", "./game_profiles"))
TEMPLATES_DIR = os.path.abspath(os.getenv("TEMPLATES_DIR", "./templates"))


class GameBot:
    def __init__(self, acc_id, loop, app):
        self.acc_id = acc_id
        self.loop = loop
        self.app = app
        self.is_running = False
        self.page = None
        self.context = None
        self.profile_path = os.path.join(BASE_PROFILES_PATH, f"account_{self.acc_id}")

        # Таймеры для контроля зависаний
        self.last_action_time = 0
        self.last_reload_time = 0

    async def send_tg_notification(self, text):
        if not TG_TOKEN or ADMIN_ID == 1:
            print(f"🤖 [АККАУНТ #{self.acc_id}][TG Alert] {text}")
            return
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        message = f"🤖 [АККАУНТ #{self.acc_id}]\n{text}"
        payload = {
            "chat_id": ADMIN_ID,
            "text": message,
            "reply_markup": {"inline_keyboard": [[{"text": "Продолжить ✅", "callback_data": f"resume_{self.acc_id}"}]]}
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, json=payload)
        except Exception as e:
            print(f"Ошибка TG: {e}")

    async def find_template(self, template_name, confidence=0.9):
        if not self.page:
            return None
        try:
            # Проверка наличия шаблона в папке templates/ или в текущей директории
            template_path = os.path.join(TEMPLATES_DIR, template_name)
            if not os.path.exists(template_path):
                template_path = template_name

            if not os.path.exists(template_path):
                return None

            screenshot_bytes = await self.page.screenshot()
            img = cv2.imdecode(np.frombuffer(screenshot_bytes, np.uint8), cv2.IMREAD_COLOR)
            template = cv2.imread(template_path, cv2.IMREAD_COLOR)
            if template is None:
                return None
            res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val >= confidence:
                h, w = template.shape[:2]
                return (max_loc[0] + w // 2, max_loc[1] + h // 2)
        except Exception:
            return None
        return None

    async def restore_energy(self):
        print(f"[Аккаунт {self.acc_id}] Открываю рюкзак для поиска напитков...")
        try:
            await self.page.keyboard.press('i')
            await asyncio.sleep(2)

            # 1. Проверяем основной напиток
            drink_pos = await self.find_template("drink.png", confidence=0.75)
            if drink_pos:
                print(f"[Аккаунт {self.acc_id}] Нашел Энергетик (drink.png), пью х2...")
                await self.page.mouse.click(*drink_pos)
                await asyncio.sleep(1.2)
                await self.page.keyboard.press('1')
                await asyncio.sleep(3.5)
                await self.page.keyboard.press('1') # Второе подтверждение
                await asyncio.sleep(3.5)

                await self.page.keyboard.press('l')
                self.last_action_time = asyncio.get_event_loop().time()
                return True

            # 2. Проверяем Квас
            kvas_pos = await self.find_template("kvas.png", confidence=0.75)
            if kvas_pos:
                print(f"[Аккаунт {self.acc_id}] Нашел Квас (kvas.png), пью х1...")
                await self.page.mouse.click(*kvas_pos)
                await asyncio.sleep(1.2)
                await self.page.keyboard.press('1') # Одиночное подтверждение
                await asyncio.sleep(3.5)

                await self.page.keyboard.press('l')
                self.last_action_time = asyncio.get_event_loop().time()
                return True

            # 3. Если ничего не нашли
            self.is_running = False
            self.loop.call_soon_threadsafe(self.app.update_btn_text, self.acc_id, "НЕТ НАПИТКОВ")
            await self.send_tg_notification("🧃 Все напитки (включая квас) закончились!")
            await self.page.keyboard.press('l')
            await asyncio.sleep(1)
            return False

        except Exception as e:
            print(f"Ошибка при поиске напитков на аккаунте {self.acc_id}: {e}")
            return False

    async def run_logic(self):
        self.last_action_time = asyncio.get_event_loop().time()
        self.last_reload_time = asyncio.get_event_loop().time()

        while True:
            if self.is_running and self.page:
                current_time = asyncio.get_event_loop().time()
                try:
                    # 1. Глобальный релоад раз в час
                    if current_time - self.last_reload_time > 3600:
                        await self.page.reload()
                        self.last_reload_time = current_time
                        self.last_action_time = current_time
                        await asyncio.sleep(10)
                        continue

                    # 2. Детектор зависания (2 минуты)
                    if current_time - self.last_action_time > 120:
                        print(f"[Аккаунт {self.acc_id}] Зависание, перезагрузка страницы...")
                        await self.page.reload()
                        self.last_action_time = current_time
                        await asyncio.sleep(10)
                        continue

                    # ЭТАП: Поиск входа (Домика)
                    await self.page.keyboard.press('l')
                    await asyncio.sleep(1)

                    home_pos = await self.find_template("button_home.png", confidence=0.75)

                    if home_pos:
                        print(f"[Аккаунт {self.acc_id}] Домик вижу, вхожу...")
                        self.last_action_time = current_time
                        await self.page.mouse.click(*home_pos)
                        await asyncio.sleep(1)

                        while self.is_running:
                            if asyncio.get_event_loop().time() - self.last_reload_time > 3600:
                                break

                            if await self.find_template("no_supplies.png", confidence=0.92):
                                self.is_running = False
                                self.loop.call_soon_threadsafe(self.app.update_btn_text, self.acc_id, "ЖДУ ТГ")
                                await self.send_tg_notification("📦 Припасы кончились!")
                                break

                            if await self.find_template("no_energy.png", confidence=0.9):
                                success = await self.restore_energy()
                                break

                            self.last_action_time = asyncio.get_event_loop().time()
                            await self.page.keyboard.press('1')
                            await asyncio.sleep(2.9)
                            await self.page.keyboard.press('Space')
                            await asyncio.sleep(0.2)
                    else:
                        await asyncio.sleep(3)

                except Exception as e:
                    print(f"Ошибка логики {self.acc_id}: {e}")
                    await asyncio.sleep(5)
            else:
                self.last_action_time = asyncio.get_event_loop().time()
            await asyncio.sleep(1)

    async def start_browser(self, p):
        if not os.path.exists(self.profile_path): os.makedirs(self.profile_path)

        lock = os.path.join(self.profile_path, "SingletonLock")
        if os.path.exists(lock):
            try:
                os.remove(lock)
            except:
                pass

        debug_port = 9220 + self.acc_id
        self.context = await p.chromium.launch_persistent_context(
            user_data_dir=self.profile_path,
            headless=False,
            no_viewport=True,
            ignore_default_args=["--enable-automation"],
            args=[
                f"--remote-debugging-port={debug_port}",
                "--start-maximized",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-session-crashed-bubble",
                "--no-focus-on-new-window" # Запрет на перехват фокуса
            ]
        )
        self.page = self.context.pages[0]
        await self.page.goto(GAME_URL)
        await self.page.evaluate(f"document.title = 'АККАУНТ #{self.acc_id}'")
        await self.run_logic()


class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.root.title("Quarantine Multi-Acc")
        self.loop = asyncio.new_event_loop()
        self.bots = [GameBot(i, self.loop, self) for i in range(1, NUM_ACCOUNTS + 1)]
        self.btns = {}

        threading.Thread(target=self._start_async_thread, daemon=True).start()
        threading.Thread(target=self._tg_listener, daemon=True).start()
        self._setup_ui()

    def _setup_ui(self):
        for bot in self.bots:
            f = ttk.LabelFrame(self.root, text=f"Аккаунт #{bot.acc_id}")
            f.pack(fill='x', padx=10, pady=5)
            btn = ttk.Button(f, text=f"СТАРТ #{bot.acc_id}", command=lambda b=bot: self.toggle(b))
            btn.pack(fill='x', padx=5, pady=5)
            self.btns[bot.acc_id] = btn

    def update_btn_text(self, acc_id, text):
        self.btns[acc_id].config(text=text)

    def toggle(self, bot):
        bot.is_running = not bot.is_running
        txt = f"СТОП #{bot.acc_id}" if bot.is_running else f"СТАРТ #{bot.acc_id}"
        self.update_btn_text(bot.acc_id, txt)

    def _start_async_thread(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._launch())

    async def _launch(self):
        async with async_playwright() as p:
            await asyncio.gather(*[bot.start_browser(p) for bot in self.bots])

    def _tg_listener(self):
        if not TG_TOKEN or ADMIN_ID == 1:
            return
        last_update_id = 0
        while True:
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.get(
                        f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                        params={"offset": last_update_id + 1, "timeout": 20}
                    ).json()
                    for upd in resp.get("result", []):
                        last_update_id = upd["update_id"]
                        if "callback_query" in upd:
                            cb = upd["callback_query"]
                            if cb.get("from", {}).get("id") == ADMIN_ID:
                                data = cb.get("data", "")
                                if data.startswith("resume_"):
                                    acc_id = int(data.split("_")[1])
                                    self.resume_bot(acc_id)
            except Exception:
                import time
                time.sleep(3)

    def resume_bot(self, acc_id):
        bot = self.bots[acc_id - 1]
        asyncio.run_coroutine_threadsafe(self._resume_sequence(bot), self.loop)

    async def _resume_sequence(self, bot):
        await bot.page.reload()
        await asyncio.sleep(5)
        bot.is_running = True
        self.update_btn_text(bot.acc_id, f"СТОП #{bot.acc_id}")


if __name__ == "__main__":
    if not os.path.exists(BASE_PROFILES_PATH): os.makedirs(BASE_PROFILES_PATH)
    root = tk.Tk()
    root.geometry("350x550")
    app = ControlPanel(root)
    root.mainloop()