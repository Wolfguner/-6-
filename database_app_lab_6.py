import psycopg2
from psycopg2.extras import RealDictCursor
import sys

# ==============================================================================
# КОНФИГУРАЦИЯ ПОДКЛЮЧЕНИЯ
# ==============================================================================
DB_CONFIG = {
    "dbname": "postgres",
    "user": "postgres",
    "password": "postgres",
    "host": "127.0.0.1",
    "port": "5432"
}
DSN = f"dbname={DB_CONFIG['dbname']} user={DB_CONFIG['user']} password={DB_CONFIG['password']} host={DB_CONFIG['host']} port={DB_CONFIG['port']} options='-c search_path=music_store'"



# Уровень данных (Connection + Repository)
class DatabaseManager:
    """Управление соединениями и настройка курсора."""
    def __init__(self, dsn: str):
        self.dsn = dsn

    def get_connection(self):
        # RealDictCursor возвращает строки как словари {имя_колонки: значение}
        return psycopg2.connect(self.dsn, cursor_factory=RealDictCursor)


class Repository:
    """Бизнес-логика и SQL-запросы. Изолирован от интерфейса."""
    def __init__(self, db: DatabaseManager):
        self.db = db

    # ------------------ CRUD: Заказы ------------------
    def get_orders_with_details(self):
        """READ: Вывод заказов с JOIN (отображается ФИО покупателя, а не buyer_id)"""
        sql = """
            SELECT o.order_id, o.order_date, o.status, o.total_price,
                   b.fio AS buyer_name,
                   COUNT(oi.product_id) AS items_count
            FROM orders o
            JOIN buyers b ON o.buyer_id = b.buyer_id
            LEFT JOIN order_items oi ON o.order_id = oi.order_id
            GROUP BY o.order_id, b.fio
            ORDER BY o.order_date DESC
            LIMIT 50;
        """
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()

    def create_order(self, buyer_id: int, status: str, total_price: float) -> int:
        """CREATE: Добавление заказа"""
        sql = """INSERT INTO orders (buyer_id, order_date, status, total_price) 
                 VALUES (%s, NOW(), %s, %s) RETURNING order_id;"""
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (buyer_id, status, total_price))
                conn.commit()
                return cur.fetchone()["order_id"]

    def update_order_status(self, order_id: int, new_status: str) -> bool:
        """UPDATE: Изменение статуса"""
        sql = "UPDATE orders SET status = %s WHERE order_id = %s;"
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (new_status, order_id))
                conn.commit()
                return cur.rowcount > 0

    def delete_order(self, order_id: int) -> bool:
        """DELETE: Удаление заказа"""
        sql = "DELETE FROM orders WHERE order_id = %s;"
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (order_id,))
                conn.commit()
                return cur.rowcount > 0

    # ------------------ CRUD: Покупатели ------------------
    def get_buyers(self):
        """READ: Список покупателей"""
        sql = "SELECT buyer_id, fio, email, created_at FROM buyers ORDER BY buyer_id DESC LIMIT 30;"
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()

    def create_buyer(self, fio: str, email: str) -> int:
        """CREATE: Добавление покупателя"""
        sql = "INSERT INTO buyers (fio, email) VALUES (%s, %s) RETURNING buyer_id;"
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (fio, email))
                conn.commit()
                return cur.fetchone()["buyer_id"]

    def update_buyer(self, buyer_id: int, fio: str, email: str) -> bool:
        """UPDATE: Изменение данных покупателя"""
        sql = "UPDATE buyers SET fio = %s, email = %s WHERE buyer_id = %s;"
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (fio, email, buyer_id))
                conn.commit()
                return cur.rowcount > 0

    def delete_buyer(self, buyer_id: int) -> bool:
        """DELETE: Удаление покупателя"""
        sql = "DELETE FROM buyers WHERE buyer_id = %s;"
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (buyer_id,))
                conn.commit()
                return cur.rowcount > 0

    # ------------------ Аналит. запросы ------------------
    def analytics_label_sales(self, label_name: str):
        """Запрос 14: Продажи определённого лейбла"""
        sql = """
            SELECT l.name AS label, r.name AS album, 
                   COUNT(oi.order_id) AS times_sold, 
                   SUM(oi.quantity) AS total_quantity, 
                   SUM(oi.quantity * oi.price_at_moment) AS revenue
            FROM labels l
            JOIN releases r ON l.labels_id = r.label_id
            JOIN products p ON r.release_id = p.release_id
            JOIN order_items oi ON p.product_id = oi.product_id
            JOIN orders o ON oi.order_id = o.order_id
            WHERE l.name = %s AND o.status IN ('paid', 'done')
            GROUP BY l.name, r.name
            ORDER BY revenue DESC;
        """
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (label_name,))
                return cur.fetchall()

    def analytics_returning_customers(self, min_orders: int):
        """Запрос 17: Возвращающиеся клиенты"""
        sql = """
            SELECT b.buyer_id, b.fio, 
                   COUNT(o.order_id) AS orders_count, 
                   SUM(o.total_price) AS total_spent, 
                   MAX(o.order_date) AS last_order
            FROM buyers b
            JOIN orders o ON b.buyer_id = o.buyer_id
            WHERE o.status IN ('paid', 'done')
            GROUP BY b.buyer_id, b.fio
            HAVING COUNT(o.order_id) > %s
            ORDER BY orders_count DESC;
        """
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (min_orders,))
                return cur.fetchall()

    def analytics_stale_inventory(self, days_threshold: int):
        """Запрос 20: Товар без продаж > N дней"""
        sql = """
            SELECT p.product_id, r.name AS album, p.quantity AS in_stock, 
                   p.price, p.created_at AS added_date,
                   MAX(o.order_date) AS last_sale_date,
                   CURRENT_DATE - MAX(o.order_date)::date AS days_since_last_sale
            FROM products p
            JOIN releases r ON p.release_id = r.release_id
            LEFT JOIN order_items oi ON p.product_id = oi.product_id
            LEFT JOIN orders o ON oi.order_id = o.order_id 
              AND o.status IN ('paid', 'done')
            GROUP BY p.product_id, r.name, p.quantity, p.price, p.created_at
            HAVING MAX(o.order_date) IS NULL 
                OR CURRENT_DATE - MAX(o.order_date)::date > %s
            ORDER BY days_since_last_sale DESC NULLS LAST;
        """
        with self.db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (days_threshold,))
                return cur.fetchall()



# ----------------ИНТЕРФЕЙС (UI)----------------
class ConsoleUI:
    """Консольное меню, ввод, валидация, форматирование вывода."""
    def __init__(self, repo: Repository):
        self.repo = repo

    def _print_header(self, title: str):
        """Вывод заголовка таблицы"""
        print("\n" + "=" * 80)
        print(f" {title}")
        print("=" * 80)

    def _print_separator(self):
        """Разделительная линия"""
        print("-" * 80)

    # Менюшка
    def print_menu(self):
        print("\n" + "=" * 40)
        print("База данных магазина музыки на физических носителях")
        print("=" * 40)
        print("Заказы:")
        print("  1. Список заказов")
        print("  2. Добавить заказ")
        print("  3. Изменить статус заказа")
        print("  4. Удалить заказ")
        print("Покупатели:")
        print("  5. Список покупателей")
        print("  6. Добавить покупателя")
        print("  7. Изменить покупателя")
        print("  8. Удалить покупателя")
        print("Аналитические заказы")
        print("  9. Продажи лейбла")
        print("  10. Возвращающиеся клиенты")
        print("  11. Товары без продаж > N дней")
        print("  0. Выход")
        print("=" * 40)

    # Основной цикл программы
    def run(self):
        while True:
            self.print_menu()
            choice = input("Выберите действие: ").strip()
            try:
                if choice == "1": self._cmd_orders()
                elif choice == "2": self._cmd_create_order()
                elif choice == "3": self._cmd_update_order()
                elif choice == "4": self._cmd_delete_order()
                elif choice == "5": self._cmd_buyers()
                elif choice == "6": self._cmd_create_buyer()
                elif choice == "7": self._cmd_update_buyer()
                elif choice == "8": self._cmd_delete_buyer()
                elif choice == "9": self._cmd_label_sales()
                elif choice == "10": self._cmd_returning_customers()
                elif choice == "11": self._cmd_stale_inventory()
                elif choice == "0":
                    print("Завершение работы. Ресурсы освобождены.")
                    break
                else:
                    print("Неизвестная команда.")
            except ValueError:
                print("Ошибка ввода: ожидаются числа или корректные строки.")
            except psycopg2.Error as e:
                print(f"Ошибка БД: {e}")
            except Exception as e:
                print(f"Неизвестная ошибка: {e}")

    # --- CRUD Handlers ---
    # Вывод заказов
    def _cmd_orders(self):
        rows = self.repo.get_orders_with_details()
        if not rows:
            print("Заказов нет.")
            return

        self._print_header("СПИСОК ЗАКАЗОВ")

        # Заголовки колонок с фиксированной шириной
        header = f"{'ID':<8} {'Дата':<12} {'Статус':<10} {'Сумма':>12} {'Покупатель':<30}"
        print(header)
        self._print_separator()

        # Данные
        for r in rows:
            order_id = str(r['order_id'])
            date = str(r['order_date'])[:10]
            status = r['status']
            price = f"{r['total_price']:>10.2f}"
            buyer = r['buyer_name'][:28] + ".." if len(r['buyer_name']) > 30 else r['buyer_name']

            row = f"{order_id:<8} {date:<12} {status:<10} {price:>12} {buyer:<30}"
            print(row)
            self._print_separator()

        print(f"Всего записей: {len(rows)}\n")

    # Создание нового заказа
    def _cmd_create_order(self):
        bid = int(input("Buyer ID: "))
        status = input("Status (draft/paid/done): ").strip().lower()
        price = float(input("Total Price: "))
        oid = self.repo.create_order(bid, status, price)
        print(f"Заказ #{oid} успешно создан.")

    # Обновление существущего заказа
    def _cmd_update_order(self):
        oid = int(input("Order ID: "))
        status = input("New Status: ").strip().lower()
        ok = self.repo.update_order_status(oid, status)
        print("Статус обновлён." if ok else "Заказ не найден.")

    # Удаление заказа
    def _cmd_delete_order(self):
        oid = int(input("Order ID для удаления: "))
        ok = self.repo.delete_order(oid)
        print("Заказ удалён." if ok else "Заказ не найден.")

    # Обработка пользовательского ввода для CRUD запросов к таблице buyers
    # Вывод клиентов
    def _cmd_buyers(self):
        rows = self.repo.get_buyers()
        if not rows:
            print("Покупателей нет.")
            return

        self._print_header("СПИСОК ПОКУПАТЕЛЕЙ")

        # Заголовки
        header = f"{'ID':<6} {'ФИО':<35} {'Email':<25} {'Дата рег.':<12}"
        print(header)
        self._print_separator()

        # Данные
        for r in rows:
            buyer_id = str(r['buyer_id'])
            fio = r['fio'][:33] + ".." if len(r['fio']) > 35 else r['fio']
            email = r['email'][:23] + ".." if len(r['email']) > 25 else r['email']
            reg_date = str(r['created_at'])[:10]

            row = f"{buyer_id:<6} {fio:<35} {email:<25} {reg_date:<12}"
            print(row)
            self._print_separator()

        print(f"Всего записей: {len(rows)}\n")

    # Создание покупателя
    def _cmd_create_buyer(self):
        fio = input("ФИО: ").strip()
        email = input("Email: ").strip()
        bid = self.repo.create_buyer(fio, email)
        print(f"Покупатель #{bid} добавлен.")

    # обновление существующего покупателя
    def _cmd_update_buyer(self):
        bid = int(input("Buyer ID: "))
        fio = input("Новое ФИО: ").strip()
        email = input("Новый Email: ").strip()
        ok = self.repo.update_buyer(bid, fio, email)
        print("Покупатель обновлён." if ok else "Покупатель не найден.")

    # Удаление покупателя
    def _cmd_delete_buyer(self):
        bid = int(input("Buyer ID для удаления: "))
        ok = self.repo.delete_buyer(bid)
        print("Покупатель удалён." if ok else "Покупатель не найден.")

    # --- Analytics Handlers ---
    # Количество продаж определенного лейбла
    def _cmd_label_sales(self):
        label = input("Введите название лейбла (например Apex Audio): ").strip()
        rows = self.repo.analytics_label_sales(label)
        if not rows:
            print("Нет продаж для этого лейбла.")
            return

        self._print_header(f"ПРОДАЖИ ЛЕЙБЛА: {label.upper()}")

        header = f"{'Альбом':<35} {'Продаж':>8} {'Штук':>6} {'Выручка':>12}"
        print(header)
        self._print_separator()

        for r in rows:
            album = r['album'][:33] + ".." if len(r['album']) > 35 else r['album']
            times_sold = str(r['times_sold'])
            qty = str(r['total_quantity'])
            revenue = f"{r['revenue']:>10.2f}"

            row = f"{album:<35} {times_sold:>8} {qty:>6} {revenue:>12}"
            print(row)
            self._print_separator()

        print(f"Всего альбомов: {len(rows)}\n")

    # Возвращающиеся клиенты
    def _cmd_returning_customers(self):
        min_ord = int(input("Минимальное кол-во заказов (например 1): "))
        rows = self.repo.analytics_returning_customers(min_ord)
        if not rows:
            print("Нет клиентов с таким количеством заказов.")
            return

        self._print_header(f"ВОЗВРАЩАЮЩИЕСЯ КЛИЕНТЫ (> {min_ord} зак.)")

        header = f"{'ID':<6} {'ФИО':<30} {'Заказов':>8} {'Потрачено':>12} {'Посл. покупка':<12}"
        print(header)
        self._print_separator()

        for r in rows:
            buyer_id = str(r['buyer_id'])
            fio = r['fio'][:28] + ".." if len(r['fio']) > 30 else r['fio']
            orders = str(r['orders_count'])
            spent = f"{r['total_spent']:>10.2f}"
            last_order = str(r['last_order'])[:10]

            row = f"{buyer_id:<6} {fio:<30} {orders:>8} {spent:>12} {last_order:<12}"
            print(row)
            self._print_separator()

        print(f"Всего клиентов: {len(rows)}\n")

    # Залежавшиеся товары (нет продаж более n дней)
    def _cmd_stale_inventory(self):
        days = int(input("Дней без продаж (например 30): "))
        rows = self.repo.analytics_stale_inventory(days)
        if not rows:
            print("Все товары продаются активно.")
            return

        self._print_header(f"ТОВАРЫ БЕЗ ПРОДАЖ (> {days} дн.)")

        header = f"{'ID':<6} {'Альбом':<35} {'Остаток':>7} {'Цена':>10} {'Дней':>8}"
        print(header)
        self._print_separator()

        for r in rows:
            prod_id = str(r['product_id'])
            album = r['album'][:33] + ".." if len(r['album']) > 35 else r['album']
            stock = str(r['in_stock'])
            price = f"{r['price']:>8.2f}"

            if r['days_since_last_sale'] is not None:
                days_val = f"{r['days_since_last_sale']} дн."
            else:
                days_val = "Никогда"

            row = f"{prod_id:<6} {album:<35} {stock:>7} {price:>10} {days_val:>8}"
            print(row)
            self._print_separator()

        print(f"Всего позиций: {len(rows)}\n")


if __name__ == "__main__":
    try:
        db = DatabaseManager(DSN)
        repo = Repository(db)
        app = ConsoleUI(repo)
        print("Подключение к PostgreSQL успешно.")
        app.run()
    except psycopg2.OperationalError as e:
        print(f"Не удалось подключиться к БД: {e}")
        print("Проверьте DB_CONFIG и убедитесь, что PostgreSQL запущен.")
        sys.exit(1)