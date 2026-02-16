"""CookieCloud客户端，用于从CookieCloud服务器获取cookies"""
import logging
import requests
from typing import Dict, List, Optional
from urllib.parse import urljoin
import urllib3

# 禁用SSL警告，因为我们可能连接到使用自签名证书的服务器
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from javsp.config import Cfg

logger = logging.getLogger(__name__)


class CookieCloudClient:
    """CookieCloud客户端"""
    
    def __init__(self, server_url: str, uuid: str, password: str):
        """
        初始化CookieCloud客户端
        
        Args:
            server_url: CookieCloud服务器地址，例如: http://localhost:8088
            uuid: CookieCloud的UUID
            password: CookieCloud的密码
        """
        self.server_url = server_url.rstrip('/')
        self.uuid = uuid
        self.password = password
        self._cookies_cache: Optional[Dict[str, Dict[str, str]]] = None
    
    def get_cookies(self, domain: str = None) -> Dict[str, Dict[str, str]]:
        """
        从CookieCloud获取cookies
        
        Args:
            domain: 可选，指定域名过滤cookies
            
        Returns:
            字典，格式为 {domain: {cookie_name: cookie_value}}
        """
        if self._cookies_cache is not None:
            # 使用缓存
            if domain:
                return {k: v for k, v in self._cookies_cache.items() if domain in k}
            return self._cookies_cache
        
        try:
            # 根据诊断结果，使用简化格式: /get/{uuid}
            # 密码通过Authorization header传递
            api_url = urljoin(self.server_url, f'/get/{self.uuid}')

            logger.debug(f'使用CookieCloud API: {api_url}')

            # 设置Authorization header（如果服务器需要的话）
            headers = {}
            if self.password:
                # 尝试Basic Auth格式
                import base64
                auth_string = base64.b64encode(f'{self.uuid}:{self.password}'.encode()).decode()
                headers['Authorization'] = f'Basic {auth_string}'

            # 禁用SSL证书验证，适用于自签名证书或不受信任的证书
            response = requests.get(api_url, headers=headers, timeout=10, verify=False)

            response.raise_for_status()

            data = response.json()
            logger.debug(f'CookieCloud响应: {data}')

            # 处理加密的响应
            if isinstance(data, list) and len(data) == 1 and data[0] == 'encrypted':
                error_msg = 'CookieCloud返回加密数据，目前不支持解密'
                logger.warning(error_msg)
                logger.info('解决方案：')
                logger.info('1. 检查CookieCloud服务器配置，禁用数据加密功能')
                logger.info('2. 在CookieCloud管理界面查找"加密传输"或"数据加密"选项并关闭')
                logger.info('3. 重启CookieCloud服务器后重新尝试同步')
                logger.info('4. 临时解决方案：手动配置cookies或使用浏览器导出cookies')
                # 返回特殊的错误信息，让Web界面能显示给用户
                return {'__error__': {
                    'type': 'encryption',
                    'message': error_msg,
                    'solutions': [
                        '检查CookieCloud服务器配置，禁用数据加密功能',
                        '在CookieCloud管理界面查找"加密传输"或"数据加密"选项并关闭',
                        '重启CookieCloud服务器后重新尝试同步',
                        '临时解决方案：手动配置cookies或使用浏览器导出cookies'
                    ]
                }}
            # 处理字典格式的加密响应
            elif isinstance(data, dict) and 'encrypted' in data:
                error_msg = 'CookieCloud返回字典格式的加密数据，目前不支持解密'
                logger.warning(error_msg)
                logger.info('解决方案：')
                logger.info('1. 检查CookieCloud服务器配置，确保数据传输不加密')
                logger.info('2. 在CookieCloud管理界面查找"数据加密"相关设置并关闭')
                logger.info('3. 如果使用Docker版本，检查环境变量或配置文件')
                logger.info('4. 联系CookieCloud开发者了解最新解密方法')
                logger.info('5. 临时解决方案：手动配置cookies或使用浏览器导出cookies')
                # 返回特殊的错误信息，让Web界面能显示给用户
                return {'__error__': {
                    'type': 'encryption_dict',
                    'message': error_msg,
                    'solutions': [
                        '检查CookieCloud服务器配置，确保数据传输不加密',
                        '在CookieCloud管理界面查找"数据加密"相关设置并关闭',
                        '如果使用Docker版本，检查环境变量或配置文件',
                        '联系CookieCloud开发者了解最新解密方法',
                        '临时解决方案：手动配置cookies或使用浏览器导出cookies'
                    ]
                }}
            # 处理其他可能的响应格式
            elif isinstance(data, list):
                logger.warning(f'CookieCloud返回未知的列表格式: {data}')
                return {}
            elif not isinstance(data, dict):
                logger.warning(f'CookieCloud返回未知的数据类型: {type(data)}, 值: {data}')
                return {}

            # CookieCloud返回格式: {"cookie_data": [{"domain": "...", "cookies": {...}}]}
            if data.get('status') == 'success' and 'cookie_data' in data:
                cookies_dict = {}
                for item in data['cookie_data']:
                    item_domain = item.get('domain', '')
                    item_cookies = item.get('cookies', {})
                    if item_cookies:
                        cookies_dict[item_domain] = item_cookies

                self._cookies_cache = cookies_dict
                logger.info(f'成功从CookieCloud获取到 {len(cookies_dict)} 个域名的cookies')

                if domain:
                    return {k: v for k, v in cookies_dict.items() if domain in k}
                return cookies_dict
            else:
                error_msg = data.get('message', '未知错误')
                logger.warning(f'CookieCloud返回错误: {error_msg}')
                return {}
                
        except requests.exceptions.RequestException as e:
            logger.warning(f'无法连接到CookieCloud服务器 ({self.server_url}): {e}')
            return {}
        except Exception as e:
            logger.warning(f'从CookieCloud获取cookies时出错: {e}', exc_info=True)
            return {}
    
    def get_cookies_for_domain(self, domain: str) -> Dict[str, str]:
        """
        获取指定域名的cookies
        
        Args:
            domain: 域名，例如: javdb.com
            
        Returns:
            字典，格式为 {cookie_name: cookie_value}
        """
        all_cookies = self.get_cookies()
        
        # 精确匹配
        if domain in all_cookies:
            return all_cookies[domain]
        
        # 模糊匹配（包含该域名的）
        for cookie_domain, cookies in all_cookies.items():
            if domain in cookie_domain or cookie_domain in domain:
                return cookies
        
        return {}
    
    def clear_cache(self):
        """清除缓存，强制下次重新获取"""
        self._cookies_cache = None

    def sync_cookies(self) -> bool:
        """
        强制从CookieCloud同步最新的cookies

        Returns:
            bool: 同步是否成功
        """
        try:
            # 清除缓存
            self.clear_cache()
            # 重新获取cookies
            cookies = self.get_cookies()
            return len(cookies) > 0
        except Exception as e:
            logger.error(f'同步CookieCloud cookies失败: {e}', exc_info=True)
            return False


def get_cookiecloud_cookies(domain: str = None) -> Dict[str, Dict[str, str]]:
    """
    从配置的CookieCloud服务器获取cookies

    Args:
        domain: 可选，指定域名过滤cookies

    Returns:
        字典，格式为 {domain: {cookie_name: cookie_value}}
    """
    cfg = Cfg()
    cookiecloud = cfg.network.cookiecloud

    if not cookiecloud.enabled:
        return {}

    if not cookiecloud.server_url or not cookiecloud.uuid or not cookiecloud.password:
        logger.debug('CookieCloud未完整配置（缺少server_url、uuid或password）')
        return {}

    try:
        client = CookieCloudClient(
            server_url=cookiecloud.server_url,
            uuid=cookiecloud.uuid,
            password=cookiecloud.password
        )
        return client.get_cookies(domain=domain)
    except Exception as e:
        logger.warning(f'初始化CookieCloud客户端失败: {e}', exc_info=True)
        return {}


def sync_cookiecloud_cookies() -> bool:
    """
    强制同步CookieCloud的cookies

    Returns:
        bool: 同步是否成功
    """
    cfg = Cfg()
    cookiecloud = cfg.network.cookiecloud

    if not cookiecloud.enabled:
        logger.debug('CookieCloud未启用，无法同步')
        return False

    if not cookiecloud.server_url or not cookiecloud.uuid or not cookiecloud.password:
        logger.debug('CookieCloud未完整配置，无法同步')
        return False

    try:
        client = CookieCloudClient(
            server_url=cookiecloud.server_url,
            uuid=cookiecloud.uuid,
            password=cookiecloud.password
        )
        return client.sync_cookies()
    except Exception as e:
        logger.error(f'同步CookieCloud cookies失败: {e}', exc_info=True)
        return False

