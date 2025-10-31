import logging
from os import makedirs, path
from glob import glob
from datetime import datetime
import pytz
from zzupy.app import CASClient, ECardClient
import requests
import json
import os
import smtplib
from email.mime.text import MIMEText
from time import sleep

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 电量阈值
THRESHOLD = 5.0
EXCELLENT_THRESHOLD = 100.0

# 数据存储文件夹路径
JSON_FOLDER_PATH = "./page/data"

# 环境变量
ACCOUNT = os.getenv("ACCOUNT")
PASSWORD = os.getenv("PASSWORD")
lt_room = os.getenv("lt_room")
ac_room = os.getenv("ac_room")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SERVERCHAN_KEYS = os.getenv("SERVERCHAN_KEYS")
MAIL = os.getenv("EMAIL")
SMTP_CODE = os.getenv("SMTP_CODE")
SMTP_SERVER = os.getenv("SMTP_SERVER")

class EnergyMonitor:
    def __init__(self):
        self.cas = CASClient(ACCOUNT, PASSWORD)

    def get_energy_balance(self, max_retries: int = 3, retry_delay: float = 2.0):
        """使用 ZZUPy 获取电量余额，支持失败重试"""
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[第 {attempt} 次尝试] 登录统一认证系统...")
                self.cas.login()
                logger.info("统一认证登录成功")
                
                logger.info(f"[第 {attempt} 次尝试] 登录一卡通系统...")
                with ECardClient(self.cas) as ecard:
                    ecard.login()
                    logger.info("一卡通系统登录成功")
    
                    logger.info("获取照明和空调电量余额...")
                    lt_balance = ecard.get_remaining_energy(lt_room)
                    ac_balance = ecard.get_remaining_energy(ac_room)
                    logger.info(f"照明剩余电量：{lt_balance} 度，空调剩余电量：{ac_balance} 度")
    
                    return {"lt_Balance": lt_balance, "ac_Balance": ac_balance}
    
            except Exception as e:
                logger.warning(f"第 {attempt} 次尝试失败：{e}")
                if attempt < max_retries:
                    logger.info(f"{retry_delay} 秒后重试...")
                    sleep(retry_delay)
                else:
                    logger.error("超过最大重试次数，获取电量失败")
                    raise  # 抛出异常让上层处理
    

class NotificationManager:
    @staticmethod
    def format_balance_report(lt_balance, ac_balance):
        """格式化电量报告信息，按照电量状态生成充足/还行/警告的提示信息"""
        def get_status(balance):
            if balance > EXCELLENT_THRESHOLD:
                return "充足"
            elif balance > THRESHOLD:
                return "还行"
            else:
                return "⚠️警告"

        lt_status = get_status(lt_balance)
        ac_status = get_status(ac_balance)

        # 对电量值中的 '.' 进行转义
        lt_balance_escaped = str(lt_balance).replace(".", "\\.")
        ac_balance_escaped = str(ac_balance).replace(".", "\\.")

        report = (
            f"💡 照明剩余电量：{lt_balance_escaped} 度（{lt_status}）\n"
            f"❄️ 空调剩余电量：{ac_balance_escaped} 度（{ac_status}）\n\n"
        )
        return report

    @staticmethod
    def notify_admin(title, content):
        """通过 Server 酱和 Telegram 发送通知"""
        logger.info("准备发送通知...")

        if "⚠️警告" in content:
            logger.info("电量低于阈值，通过 Server 酱发送通知...")
            for key in SERVERCHAN_KEYS.split(','):
                if key:
                    url = f"https://sctapi.ftqq.com/{key}.send"
                    payload = {"title": title, "desp": content}
                    response = requests.post(url, data=payload)
                    result = response.json()
                    if result.get("code") == 0:
                        logger.info(f"Server 酱通知发送成功，使用的密钥：{key}")
                    else:
                        logger.error(f"Server 酱通知发送失败，错误信息：{result.get('message')}")


            logger.info("电量低于阈值，通过邮件发送通知...")
            msg = MIMEText(content, 'plain', 'utf-8')

            msg['Subject'] = title
            msg['From'] = MAIL
            msg['To'] = MAIL

            try:
                client = smtplib.SMTP_SSL(SMTP_SERVER, smtplib.SMTP_SSL_PORT)
                logger.info("连接到邮件服务器成功")
            
                client.login(MAIL, SMTP_CODE)
                logger.info("登录成功")
            
                client.sendmail(MAIL, MAIL, msg.as_string())
                logger.info("发送成功")
                
            except smtplib.SMTPException as e:
                logger.error("发送邮件异常")
            finally:
                client.quit()
                
        logger.info("通过 Telegram 发送通知...")
        NotificationManager.notify_telegram(title, content)

    @staticmethod
    def notify_telegram(title, content):
        """发送 Telegram 通知"""
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"*{title}*\n\n{content}",
            "parse_mode": "MarkdownV2"
        }
        response = requests.post(url, data=payload)
        result = response.json()
        if result.get("ok"):
            logger.info("Telegram 通知发送成功")
        else:
            logger.error(f"Telegram 通知发送失败，错误信息：{result.get('description')}")

class DataManager:
    @staticmethod
    def load_data_from_json(file_path: str) -> list[dict] | None:
        """从 JSON 文件加载数据"""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            logger.warning(f"文件未找到：{file_path}")
            return []
        except json.JSONDecodeError:
            logger.error(f"文件内容无法解析为 JSON：{file_path}")
            return []

    @staticmethod
    def dump_data_into_json(data: list | dict, file_path: str, indent: int = 4):
        """将数据保存到 JSON 文件中"""
        try:
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=indent)
            logger.info(f"数据成功保存到文件：{file_path}")
        except Exception as e:
            logger.error(f"保存数据到文件失败：{file_path}，错误信息：{e}")

    @staticmethod
    def get_cst_time_str(format: str) -> str:
        """获取当前 CST（北京时间）并按照指定格式返回"""
        cst_tz = pytz.timezone('Asia/Shanghai')  # 上海时区（即北京时间）
        cst_time = datetime.now(cst_tz)
        return cst_time.strftime(format)

    @staticmethod
    def record_data(data: dict | list) -> list[dict] | None:
        """将最新的电量数据记录到 JSON 文件"""
        file_path = f"{JSON_FOLDER_PATH}/{DataManager.get_cst_time_str('%Y-%m')}.json"
        result = DataManager.load_data_from_json(file_path) or []

        if result and result[-1]["lt_Balance"] == data["lt_Balance"] and result[-1]["ac_Balance"] == data["ac_Balance"]:
            logger.info("最新数据与最后一条记录一致，跳过保存")
            return result

        result.append(data)
        DataManager.dump_data_into_json(result, file_path)
        return result

    @staticmethod
    def update_time_list() -> list[str]:
        """更新时间列表，获取存储的所有 JSON 文件名"""
        if not path.exists(JSON_FOLDER_PATH):
            raise FileNotFoundError(f"文件夹路径不存在：{JSON_FOLDER_PATH}")

        json_files = [path.splitext(path.basename(it))[0] for it in glob(path.join(JSON_FOLDER_PATH, "????-??.json"))]
        json_files = sorted(json_files, key=lambda x: datetime.strptime(x, '%Y-%m'), reverse=True)
        DataManager.dump_data_into_json(json_files, "./page/time.json")
        logger.info("时间列表更新成功")
        return json_files

    @staticmethod
    def parse_and_update_data(existing_data):
        """解析并更新数据，确保最多保留 30 条记录"""
        MAX_DISPLAY_NUM = 30
        time_file_list = DataManager.update_time_list()
        existing_data_length = len(existing_data)

        if existing_data_length < MAX_DISPLAY_NUM and len(time_file_list) > 1:
            records_to_retrieve = min(MAX_DISPLAY_NUM - existing_data_length, len(DataManager.load_data_from_json(f"{JSON_FOLDER_PATH}/{time_file_list[1]}.json")))
            existing_data = DataManager.load_data_from_json(f"{JSON_FOLDER_PATH}/{time_file_list[1]}.json")[-records_to_retrieve:] + existing_data

        DataManager.dump_data_into_json(existing_data[-MAX_DISPLAY_NUM:], f"{JSON_FOLDER_PATH}/last_30_records.json")
        logger.info("数据解析和更新完成")

def main():
    logger.info("启动宿舍电量监控程序...")
    monitor = EnergyMonitor()
    balances = monitor.get_energy_balance()

    report_content = NotificationManager.format_balance_report(balances["lt_Balance"], balances["ac_Balance"])

    if balances['lt_Balance'] <= THRESHOLD or balances['ac_Balance'] <= THRESHOLD:
        report_content += "⚠️ 电量不足，请尽快充电！"
        NotificationManager.notify_admin("⚠️宿舍电量预警⚠️", report_content)
    else:
        report_content += "请及时关注电量，避免设备关闭。"
        NotificationManager.notify_admin("🏠宿舍电量通报🏠", report_content)

    latest_record = {
        "time": DataManager.get_cst_time_str("%m-%d %H:%M:%S"),
        "lt_Balance": balances["lt_Balance"],
        "ac_Balance": balances["ac_Balance"]
    }
    data = DataManager.record_data(latest_record)
    DataManager.parse_and_update_data(data)
    logger.info("程序运行结束")

if __name__ == "__main__":
    main()
