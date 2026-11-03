import logging
import pandas as pd
import urllib.parse
from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

class DatabaseScope:
    """
        db_type: 'sqlserver', 'mysql', 'postgresql', 'sqlite', atau 'custom'
        Untuk SQL Server Windows Auth, cukup kosongkan username & password.
    """
    def __init__(self, db_type: str = "custom", server: str = "", database: str = "", 
                 username: str = "", password: str = "", connection_string: str = "", 
                 driver: str = "ODBC Driver 17 for SQL Server"):
        self.db_type = db_type.lower()
        self.engine = None
        self.connection = None

        safe_password = urllib.parse.quote_plus(password) if password else ""

        if self.db_type == "custom":
            if not connection_string:
                raise ValueError("Untuk db_type 'custom', parameter connection_string wajib diisi.")
            self.connection_string = connection_string
        elif self.db_type == "sqlite":
            if not database:
                raise ValueError("Untuk SQLite, parameter 'database' (path file) wajib diisi.")
            self.connection_string = f"sqlite:///{database}"
        elif self.db_type == "sqlserver":
            driver_fmt = driver.replace(" ", "+")
            if username and password:
                self.connection_string = f"mssql+pyodbc://{username}:{safe_password}@{server}/{database}?driver={driver_fmt}"
            else:
                self.connection_string = f"mssql+pyodbc://@{server}/{database}?driver={driver_fmt}&Trusted_Connection=yes"
        elif self.db_type == "mysql":
            self.connection_string = f"mysql+pymysql://{username}:{safe_password}@{server}/{database}"
        elif self.db_type in ["postgresql", "postgres"]:
            self.connection_string = f"postgresql://{username}:{safe_password}@{server}/{database}"
        else:
            raise ValueError(f"Tipe database '{db_type}' tidak didukung.")

    def connect(self):
        """Membuka koneksi database."""
        if self.connection is not None:
            return self

        engine_kwargs = {}
        if self.db_type == "sqlserver":
            engine_kwargs["fast_executemany"] = True
            
        self.engine = create_engine(self.connection_string, **engine_kwargs)
        self.connection = self.engine.connect()
        return self

    def disconnect(self):
        """Menutup koneksi database."""
        if self.connection:
            self.connection.close()
            self.connection = None
        if self.engine:
            self.engine.dispose()
            self.engine = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    # ==========================================
    # DATABASE ACTIVITIES
    # ==========================================
    def run_query(self, query: str, parameters: dict = None) -> pd.DataFrame:
        """Menjalankan SELECT dan mengembalikan DataFrame / Datatable."""
        if not self.connection:
            raise ConnectionError("Koneksi belum dibuka! Panggil .connect() terlebih dahulu.")
            
        df = pd.read_sql_query(text(query), self.connection, params=parameters)
        return df

    def execute_non_query(self, query: str, parameters: dict = None) -> int:
        """Menjalankan INSERT/UPDATE/DELETE (satu perintah) dan mengembalikan rowcount."""
        if not self.connection:
            raise ConnectionError("Koneksi belum dibuka! Panggil .connect() terlebih dahulu.")
            
        try:
            result = self.connection.execute(text(query), parameters or {})
            self.connection.commit()
            return result.rowcount
        except Exception:
            self.connection.rollback()
            raise

    def insert_datatable(self, table_name: str, data: pd.DataFrame, behavior: str = 'append'):
        """Memasukkan seluruh isi DataFrame ke tabel SQL secara massal.
        Gunakan behavior append untuk tambah data, atau replace untuk mereset tabel lama.
        """
        if not self.connection:
            raise ConnectionError("Koneksi belum dibuka! Panggil .connect() terlebih dahulu.")
            
        data_clean = data.where(pd.notnull(data), None)
        data_clean.to_sql(name=table_name, con=self.connection, if_exists=behavior, index=False)
        self.connection.commit()

    def bulk_update(self, table_name: str, data: pd.DataFrame, match_columns: list, batch_size: int = 1000) -> int:
        """
        Memperbarui banyak baris sekaligus berdasarkan kolom kunci (match_columns).
        Pastikan DataFrame 'data' berisi kolom yang ingin diubah + kolom kunci.
        
        :param batch_size: Jumlah rows per batch commit (default 1000). 
                           Mencegah table lock berkepanjangan pada big data.
        """
        if not self.connection:
            raise ConnectionError("Koneksi belum dibuka! Panggil .connect() terlebih dahulu.")
            
        if data.empty:
            return 0

        import numpy as np
        df_clean = data.replace([np.inf, -np.inf], np.nan).infer_objects(copy=False)
        df_clean = df_clean.astype(object).where(pd.notnull(df_clean), None)

        update_cols = [c for c in df_clean.columns if c not in match_columns]
        
        if not update_cols:
            raise ValueError("Dataframe tidak memiliki kolom sisa untuk di-update selain dari match_columns.")
        
        set_clause = ", ".join([f"[{c}] = :{c}" for c in update_cols])
        where_clause = " AND ".join([f"[{c}] = :{c}" for c in match_columns])
        sql_query = f"UPDATE [{table_name}] SET {set_clause} WHERE {where_clause}"
        
        all_params = df_clean.to_dict('records')
        total_affected = 0
        
        # Proses per batch agar tidak lock tabel terlalu lama
        for i in range(0, len(all_params), batch_size):
            batch = all_params[i:i + batch_size]
            try:
                result = self.connection.execute(text(sql_query), batch)
                self.connection.commit()
                total_affected += result.rowcount
            except Exception:
                self.connection.rollback()
                raise
        
        return total_affected

    def bulk_upsert(self, table_name: str, data: pd.DataFrame, match_columns: list) -> dict:
        """
        High-performance upsert menggunakan temp table + MERGE statement.
        Jauh lebih cepat dari bulk_update untuk data besar (>10k rows).
        
        Alur:
        1. Buat temp table (#tmp_upsert)
        2. Bulk insert data ke temp table (via to_sql — sangat cepat)
        3. MERGE dari temp table ke target table
        4. Drop temp table
        
        :param table_name: Nama tabel target
        :param data: DataFrame berisi data untuk upsert
        :param match_columns: List kolom kunci untuk matching (WHERE clause)
        :return: Dict {"updated": int, "inserted": int}
        """
        if not self.connection:
            raise ConnectionError("Koneksi belum dibuka! Panggil .connect() terlebih dahulu.")
        
        if data.empty:
            return {"updated": 0, "inserted": 0}

        import numpy as np
        df_clean = data.replace([np.inf, -np.inf], np.nan).infer_objects(copy=False)
        df_clean = df_clean.astype(object).where(pd.notnull(df_clean), None)

        temp_table = "#tmp_upsert"
        update_cols = [c for c in df_clean.columns if c not in match_columns]

        try:
            # 1. Drop temp table jika ada dari session sebelumnya
            self.connection.execute(text(f"IF OBJECT_ID('tempdb..{temp_table}') IS NOT NULL DROP TABLE {temp_table}"))
            self.connection.commit()

            # 2. Bulk insert ke temp table (sangat cepat via to_sql)
            df_clean.to_sql(name=temp_table.lstrip("#"), con=self.connection, 
                           if_exists="replace", index=False, 
                           schema="tempdb..#" if False else None)
            # to_sql tidak support # prefix, jadi kita buat manual
            # Buat temp table dengan struktur yang sama
            col_defs = ", ".join([f"[{c}] NVARCHAR(MAX)" for c in df_clean.columns])
            self.connection.execute(text(f"CREATE TABLE {temp_table} ({col_defs})"))
            self.connection.commit()

            # Insert data ke temp table per batch
            if not df_clean.empty:
                cols = df_clean.columns.tolist()
                placeholders = ", ".join([f":{c}" for c in cols])
                col_names = ", ".join([f"[{c}]" for c in cols])
                insert_sql = f"INSERT INTO {temp_table} ({col_names}) VALUES ({placeholders})"
                
                records = df_clean.to_dict('records')
                batch_size = 5000
                for i in range(0, len(records), batch_size):
                    batch = records[i:i + batch_size]
                    self.connection.execute(text(insert_sql), batch)
                self.connection.commit()

            # 3. MERGE statement
            on_clause = " AND ".join([f"target.[{c}] = source.[{c}]" for c in match_columns])
            
            result = {"updated": 0, "inserted": 0}
            
            # UPDATE yang sudah ada
            if update_cols:
                set_clause = ", ".join([f"target.[{c}] = source.[{c}]" for c in update_cols])
                update_sql = f"""
                    UPDATE target
                    SET {set_clause}
                    FROM [{table_name}] AS target
                    INNER JOIN {temp_table} AS source ON {on_clause}
                """
                res = self.connection.execute(text(update_sql))
                result["updated"] = res.rowcount if res.rowcount >= 0 else 0
                self.connection.commit()

            # INSERT yang baru
            col_names = ", ".join([f"[{c}]" for c in df_clean.columns])
            source_cols = ", ".join([f"source.[{c}]" for c in df_clean.columns])
            insert_sql = f"""
                INSERT INTO [{table_name}] ({col_names})
                SELECT {source_cols}
                FROM {temp_table} AS source
                LEFT JOIN [{table_name}] AS target ON {on_clause}
                WHERE target.[{match_columns[0]}] IS NULL
            """
            res = self.connection.execute(text(insert_sql))
            result["inserted"] = res.rowcount if res.rowcount >= 0 else 0
            self.connection.commit()

            # 4. Cleanup
            self.connection.execute(text(f"DROP TABLE {temp_table}"))
            self.connection.commit()

            return result

        except Exception:
            self.connection.rollback()
            # Cleanup temp table on error
            try:
                self.connection.execute(text(f"IF OBJECT_ID('tempdb..{temp_table}') IS NOT NULL DROP TABLE {temp_table}"))
                self.connection.commit()
            except Exception:
                pass
            raise


import threading
import queue
import logging
import pandas as pd
import socket
import os

class DatabaseLogHandler(logging.Handler):
    def __init__(self, db_connection_string: str, table_name: str = "Log_RPA", process_name: str = "RPA_Bot", capacity: int = 50):
        super().__init__()
        self.db_scope = DatabaseScope(db_type="custom", connection_string=db_connection_string)
        self.table_name = table_name
        self.process_name = process_name
        self.capacity = capacity
        self.buffer = []
        
        import uuid
        self.execution_id = str(uuid.uuid4())
        
        self.hostname = os.environ.get('COMPUTERNAME', socket.gethostname())
        
        domain = os.environ.get('USERDOMAIN', self.hostname)
        username = os.environ.get('USERNAME', 'System')
        self.host_identity = f"{domain}\\{username}"
        
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def emit(self, record):
        try:
            from datetime import datetime
            
            log_entry = {
                "Log_ID": self.execution_id,
                "Log_Time": datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'),
                "Log_Process_Name": self.process_name,
                "Log_Hostname": self.hostname,
                "Log_Host_Identity": self.host_identity,
                "Log_Type": record.levelname,
                "Log_Message": record.getMessage()
            }
            self.buffer.append(log_entry)
            
            if len(self.buffer) >= self.capacity:
                self.flush()
        except Exception:
            pass

    def flush(self):
        if self.buffer:
            self.log_queue.put(list(self.buffer))
            self.buffer.clear()

    def _worker_loop(self):
        while not self.stop_event.is_set() or not self.log_queue.empty():
            try:
                records = self.log_queue.get(timeout=1.0)
                
                try:
                    df_logs = pd.DataFrame(records)
                    
                    with self.db_scope as db:
                        db.insert_datatable(self.table_name, df_logs)
                        
                except Exception as e:
                    print(f"[DatabaseLogHandler] Gagal kirim log ke SQL: {e}")
                finally:
                    self.log_queue.task_done()
                    
            except queue.Empty:
                continue
            except Exception:
                pass

    def close(self):
        self.flush()
        
        self.stop_event.set()
        if self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5.0)
            
        super().close()
