"""网络请求的统一接口"""
import os
import sys
import time
import shutil
import logging
import requests  # type: ignore
import contextlib
import cloudscraper  # type: ignore
import lxml.html  # type: ignore
from tqdm import tqdm  # type: ignore
from lxml import etree  # type: ignore
from lxml.html.clean import Cleaner  # type: ignore


# Type definitions for better type checking
class Response:
    """Response type definition for type checking"""
    status_code: int
    url: str
    headers: dict
    content: bytes
    text: str
    encoding: str
    apparent_encoding: str
    _content: bytes
    history: list

    def raise_for_status(self) -> None: ...
    def json(self): ...


from javsp.config import Cfg
from javsp.web.exceptions import *


class FlareSolverrClient:
    """FlareSolverr客户端，用于解决Cloudflare验证"""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip('/')
        self.session_id = None

    def create_session(self) -> bool:
        """创建FlareSolverr会话"""
        try:
            response = requests.post(
                f"{self.server_url}/v1",
                json={"cmd": "sessions.create"},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            if data.get('status') == 'ok':
                self.session_id = data.get('session')
                return True
            return False
        except Exception:
            return False

    def destroy_session(self) -> bool:
        """销毁FlareSolverr会话"""
        if not self.session_id:
            return True
        try:
            response = requests.post(
                f"{self.server_url}/v1",
                json={
                    "cmd": "sessions.destroy",
                    "session": self.session_id
                },
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            return data.get('status') == 'ok'
        except Exception:
            return False

    def get_cookies(self, url: str, user_agent: str = None, max_timeout: int = 60000) -> dict:
        """
        使用FlareSolverr获取网站的cookies

        Args:
            url: 要访问的URL
            user_agent: 用户代理字符串
            max_timeout: 最大超时时间（毫秒）

        Returns:
            dict: 包含cookies和状态信息的字典
        """
        if not self.session_id and not self.create_session():
            return {"status": "error", "message": "无法创建FlareSolverr会话"}

        payload = {
            "cmd": "request.get",
            "url": url,
            "session": self.session_id,
            "maxTimeout": max_timeout
        }

        if user_agent:
            payload["userAgent"] = user_agent

        try:
            response = requests.post(
                f"{self.server_url}/v1",
                json=payload,
                timeout=max_timeout // 1000 + 10  # 转换为秒并加10秒缓冲
            )
            response.raise_for_status()
            data = response.json()

            if data.get('status') == 'ok':
                solution = data.get('solution', {})
                return {
                    "status": "success",
                    "cookies": solution.get('cookies', []),
                    "user_agent": solution.get('userAgent', ''),
                    "url": solution.get('url', url),
                    "response": solution.get('response', '')
                }
            else:
                return {
                    "status": "error",
                    "message": data.get('message', '未知错误'),
                    "error": data
                }
        except requests.exceptions.Timeout:
            return {"status": "error", "message": "FlareSolverr请求超时"}
        except Exception as e:
            return {"status": "error", "message": f"FlareSolverr请求失败: {str(e)}"}


def get_flaresolverr_client() -> FlareSolverrClient | None:
    """获取FlareSolverr客户端实例"""
    flaresolverr = Cfg().network.flaresolverr
    if not flaresolverr.enabled or not flaresolverr.server_url:
        return None

    return FlareSolverrClient(str(flaresolverr.server_url))


def detect_real_error_reason(url: str, timeout: int = 10) -> str:
    """
    检测网站真正的错误原因，通过多种方式分析响应

    Args:
        url: 要检测的URL
        timeout: 超时时间（秒）

    Returns:
        str: 检测到的错误原因描述
    """
    try:
        # 使用一个干净的Request实例，避免继承其他设置
        test_request = Request(use_scraper=False)
        test_request.timeout = timeout

        # 首先尝试普通请求
        resp = test_request.get(url, delay_raise=True)

        if resp.status_code == 200:
            # 如果能正常访问，说明之前的错误判断可能有问题
            content = resp.text.lower()
            if 'not found' in content or '404' in content:
                return "页面存在但显示未找到内容 (200 OK but not found)"
            elif 'forbidden' in content or 'access denied' in content:
                return "页面存在但显示访问被拒绝 (200 OK but forbidden)"
            else:
                return f"页面正常可访问 (200 OK, {len(resp.text)} bytes)"

        elif resp.status_code == 403:
            content = resp.text.lower()
            if 'just a moment' in content or 'checking your browser' in content or 'cf-browser' in content:
                return "Cloudflare验证保护 (403 Forbidden - CF)"
            elif 'region' in content or 'not available' in content:
                return "地理位置限制 (403 Forbidden - Geo)"
            elif 'forbidden' in content or 'access denied' in content:
                return "服务器拒绝访问 (403 Forbidden)"
            else:
                return f"未知的403错误 (403 Forbidden)"

        elif resp.status_code == 404:
            return "页面不存在 (404 Not Found)"

        elif resp.status_code == 500:
            return "服务器内部错误 (500 Internal Server Error)"

        elif resp.status_code == 502:
            return "网关错误 (502 Bad Gateway)"

        elif resp.status_code == 503:
            return "服务不可用 (503 Service Unavailable)"

        elif resp.status_code == 522:
            return "连接超时/服务器宕机 (522 Connection Timed Out)"

        else:
            content = resp.text.lower()
            if 'cloudflare' in content:
                return f"Cloudflare相关错误 ({resp.status_code})"
            elif 'region' in content or 'geo' in content:
                return f"地理位置限制 ({resp.status_code})"
            else:
                return f"未知HTTP错误 ({resp.status_code})"

    except requests.exceptions.Timeout:
        return "请求超时 (Timeout)"
    except requests.exceptions.ConnectionError:
        return "连接错误 (Connection Error)"
    except requests.exceptions.RequestException as e:
        return f"网络请求错误: {str(e)}"
    except Exception as e:
        return f"检测错误时发生异常: {str(e)}"


__all__ = ['Request', 'get_html', 'post_html', 'request_get', 'resp2html', 'is_connectable', 'download', 'get_resp_text', 'read_proxy']


headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}

logger = logging.getLogger(__name__)
# 删除js脚本相关的tag，避免网页检测到没有js运行环境时强行跳转，影响调试
cleaner = Cleaner(kill_tags=['script', 'noscript'])

def read_proxy():
    if Cfg().network.proxy_server is None:
        return {}
    else:
        proxy = str(Cfg().network.proxy_server)

        # 在容器环境中，如果代理地址是127.0.0.1或localhost，自动转换为host.docker.internal
        import os
        if os.path.exists('/.dockerenv') or os.getenv('DOCKER_CONTAINER') == 'true':
            if '127.0.0.1' in proxy or 'localhost' in proxy:
                # 替换127.0.0.1或localhost为host.docker.internal
                proxy = proxy.replace('127.0.0.1', 'host.docker.internal').replace('localhost', 'host.docker.internal')

        return {'http': proxy, 'https': proxy}

# 与网络请求相关的功能汇总到一个模块中以方便处理，但是不同站点的抓取器又有自己的需求（针对不同网站
# 需要使用不同的UA、语言等）。每次都传递参数很麻烦，而且会面临函数参数越加越多的问题。因此添加这个
# 处理网络请求的类，它带有默认的属性，但是也可以在各个抓取器模块里进行进行定制
class Request():
    """作为网络请求出口并支持各个模块定制功能"""
    def __init__(self, use_scraper=False) -> None:
        # 必须使用copy()，否则各个模块对headers的修改都将会指向本模块中定义的headers变量，导致只有最后一个对headers的修改生效
        self.headers = headers.copy()
        self.cookies = {}

        self.proxies = read_proxy()
        self.timeout = Cfg().network.timeout.total_seconds()
        if not use_scraper:
            self.scraper = None
            self.__get = requests.get
            self.__post = requests.post
            self.__head = requests.head
        else:
            self.scraper = cloudscraper.create_scraper()
            self.__get = self._scraper_monitor(self.scraper.get)
            self.__post = self._scraper_monitor(self.scraper.post)
            self.__head = self._scraper_monitor(self.scraper.head)

    def _scraper_monitor(self, func):
        """监控cloudscraper的工作状态，遇到不支持的Challenge时尝试退回常规的requests请求"""
        def wrapper(*args, **kw):
            try:
                return func(*args, **kw)
            except Exception as e:
                logger.debug(f"cloudscraper失败: '{e}', 尝试退回常规的requests请求")
                try:
                    if func == self.scraper.get:
                        fallback_r = requests.get(*args, **kw)
                        # 检查是否仍然是Cloudflare错误
                        if fallback_r.status_code == 403 and b'>Just a moment...<' in fallback_r.content:
                            # 仍然是Cloudflare，添加标记让上层知道
                            fallback_r._cloudflare_blocked = True
                        return fallback_r
                    else:
                        fallback_r = requests.post(*args, **kw)
                        if fallback_r.status_code == 403 and b'>Just a moment...<' in fallback_r.content:
                            fallback_r._cloudflare_blocked = True
                        return fallback_r
                except Exception as fallback_e:
                    logger.debug(f"常规requests也失败: '{fallback_e}'")
                    raise e  # 重新抛出原始异常
        return wrapper

    def get(self, url, delay_raise=False):
        r = self.__get(url,
                      headers=self.headers,
                      proxies=self.proxies,
                      cookies=self.cookies,
                      timeout=self.timeout)
        if not delay_raise:
            # 检查是否为Cloudflare保护
            if r.status_code == 403 and hasattr(r, 'content'):
                content = r.content
                # 检查是否为Cloudflare IUAM (I'm Under Attack Mode)
                if b'>Just a moment...<' in content:
                    # 检查是否有Cloudflare标记（来自_scraper_monitor）
                    if hasattr(r, '_cloudflare_blocked'):
                        # cloudscraper和普通requests都失败了
                        logger.debug(f"cloudscraper和普通requests都无法绕过Cloudflare IUAM: {url}")
                        raise SiteBlocked(f"403 Forbidden: 无法通过CloudFlare IUAM检测 (需要等待或使用代理): {url}")
                    else:
                        # 这是第一次遇到Cloudflare，尝试使用cloudscraper（如果还没使用的话）
                        if self.scraper is None:
                            logger.debug(f"检测到Cloudflare IUAM保护，尝试使用cloudscraper绕过: {url}")
                            try:
                                scraper = cloudscraper.create_scraper()
                                fallback_r = scraper.get(url,
                                                       headers=self.headers,
                                                       proxies=self.proxies,
                                                       cookies=self.cookies,
                                                       timeout=self.timeout)
                                if fallback_r.status_code == 200:
                                    logger.debug(f"成功通过cloudscraper绕过Cloudflare IUAM: {url}")
                                    return fallback_r
                                else:
                                    logger.warning(f"cloudscraper也无法绕过Cloudflare IUAM，返回状态码: {fallback_r.status_code}")
                            except Exception as e:
                                logger.warning(f"cloudscraper绕过失败: {e}")

                        # 如果cloudscraper也失败，尝试使用FlareSolverr
                        flaresolverr_client = get_flaresolverr_client()
                        if flaresolverr_client:
                            logger.debug(f"尝试使用FlareSolverr绕过Cloudflare IUAM: {url}")
                            fs_result = flaresolverr_client.get_cookies(url)
                            if fs_result.get('status') == 'success':
                                logger.debug(f"FlareSolverr成功绕过Cloudflare IUAM: {url}")
                                # 创建一个简单的模拟Response对象
                                class MockResponse:
                                    def __init__(self):
                                        self.status_code = 200
                                        self.url = fs_result.get('url', url)
                                        self.headers = {'Content-Type': 'text/html'}
                                        self._content = fs_result.get('response', '').encode('utf-8')
                                        self.content = self._content

                                    def raise_for_status(self):
                                        pass

                                mock_response = MockResponse()
                                # 设置cookies
                                for cookie in fs_result.get('cookies', []):
                                    if 'name' in cookie and 'value' in cookie:
                                        self.cookies[cookie['name']] = cookie['value']
                                return mock_response
                            else:
                                logger.warning(f"FlareSolverr绕过失败: {fs_result.get('message', '未知错误')}")

                        # 如果都失败，抛出原异常
                        raise SiteBlocked(f"403 Forbidden: 无法通过CloudFlare IUAM检测 (需要等待或使用代理): {url}")
                # 检查是否为reCAPTCHA (需要用户交互)
                elif b'recaptcha' in content.lower() or b'g-recaptcha' in content or b'captcha' in content.lower():
                    logger.debug(f"检测到reCAPTCHA验证，需要用户交互: {url}")
                    raise SiteBlocked(f"403 Forbidden: 检测到reCAPTCHA验证 (需要用户手动完成验证): {url}")
                # 其他类型的403错误
                else:
                    logger.debug(f"403 Forbidden错误，非Cloudflare相关: {url}")
                    r.raise_for_status()
            else:
                r.raise_for_status()
        return r

    def post(self, url, data, delay_raise=False):
        r = self.__post(url,
                      data=data,
                      headers=self.headers,
                      proxies=self.proxies,
                      cookies=self.cookies,
                      timeout=self.timeout)
        if not delay_raise:
            # 检查是否为Cloudflare保护
            if r.status_code == 403 and hasattr(r, 'content') and b'>Just a moment...<' in r.content:
                # 检查是否有Cloudflare标记（来自_scraper_monitor）
                if hasattr(r, '_cloudflare_blocked'):
                    # cloudscraper和普通requests都失败了
                    logger.debug(f"cloudscraper和普通requests都无法绕过Cloudflare: {url}")
                    raise SiteBlocked(f"403 Forbidden: 无法通过CloudFlare检测: {url}")
                else:
                    # 这是第一次遇到Cloudflare，尝试使用cloudscraper（如果还没使用的话）
                    if self.scraper is None:
                        logger.debug(f"检测到Cloudflare保护，尝试使用cloudscraper绕过: {url}")
                        try:
                            scraper = cloudscraper.create_scraper()
                            fallback_r = scraper.post(url,
                                                    data=data,
                                                    headers=self.headers,
                                                    proxies=self.proxies,
                                                    cookies=self.cookies,
                                                    timeout=self.timeout)
                            if fallback_r.status_code in [200, 201, 202]:
                                logger.debug(f"成功通过cloudscraper绕过Cloudflare: {url}")
                                return fallback_r
                            else:
                                logger.warning(f"cloudscraper也无法绕过Cloudflare，返回状态码: {fallback_r.status_code}")
                        except Exception as e:
                            logger.warning(f"cloudscraper绕过失败: {e}")
                    # 如果都失败，抛出原异常
                    raise SiteBlocked(f"403 Forbidden: 无法通过CloudFlare检测: {url}")
            else:
                r.raise_for_status()
        return r

    def head(self, url, delay_raise=True):
        r = self.__head(url,
                      headers=self.headers,
                      proxies=self.proxies,
                      cookies=self.cookies,
                      timeout=self.timeout)
        if not delay_raise:
            r.raise_for_status()
        return r

    def get_html(self, url):
        r = self.get(url)
        html = resp2html(r)
        return html


class DownloadProgressBar(tqdm):
    def update_to(self, b=1, bsize=1, tsize=None):
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)


def request_get(url, cookies={}, timeout=None, delay_raise=False):
    """获取指定url的原始请求，自动尝试绕过Cloudflare"""
    if timeout is None:
        timeout = Cfg().network.timeout.seconds

    proxies = read_proxy()

    # 首先尝试普通请求
    r = requests.get(url, headers=headers, proxies=proxies, cookies=cookies, timeout=timeout)
    if not delay_raise:
        if r.status_code == 403 and b'>Just a moment...<' in r.content:
            # 检测到Cloudflare，尝试使用cloudscraper
            logger.debug(f"检测到Cloudflare保护，尝试使用cloudscraper绕过: {url}")
            try:
                scraper = cloudscraper.create_scraper()
                r = scraper.get(url, headers=headers, proxies=proxies, cookies=cookies, timeout=timeout)
                if r.status_code == 200:
                    logger.debug(f"成功通过cloudscraper绕过Cloudflare: {url}")
                    return r
                else:
                    logger.warning(f"cloudscraper也无法绕过Cloudflare，返回状态码: {r.status_code}")
            except Exception as e:
                logger.warning(f"cloudscraper绕过失败: {e}")
            # 如果cloudscraper也失败，抛出原异常
            raise SiteBlocked(f"403 Forbidden: 无法通过CloudFlare检测: {url}")
        else:
            r.raise_for_status()
    return r


def request_post(url, data, cookies={}, timeout=None, delay_raise=False):
    """向指定url发送post请求，自动尝试绕过Cloudflare"""
    if timeout is None:
        timeout = Cfg().network.timeout.seconds

    proxies = read_proxy()

    # 首先尝试普通请求
    r = requests.post(url, data=data, headers=headers, proxies=proxies, cookies=cookies, timeout=timeout)
    if not delay_raise:
        if r.status_code == 403 and b'>Just a moment...<' in r.content:
            # 检测到Cloudflare，尝试使用cloudscraper
            logger.debug(f"检测到Cloudflare保护，尝试使用cloudscraper绕过: {url}")
            try:
                scraper = cloudscraper.create_scraper()
                r = scraper.post(url, data=data, headers=headers, proxies=proxies, cookies=cookies, timeout=timeout)
                if r.status_code in [200, 201, 202]:
                    logger.debug(f"成功通过cloudscraper绕过Cloudflare: {url}")
                    return r
                else:
                    logger.warning(f"cloudscraper也无法绕过Cloudflare，返回状态码: {r.status_code}")
            except Exception as e:
                logger.warning(f"cloudscraper绕过失败: {e}")
            # 如果cloudscraper也失败，抛出原异常
            raise SiteBlocked(f"403 Forbidden: 无法通过CloudFlare检测: {url}")
        else:
            r.raise_for_status()
    return r


def get_resp_text(resp: Response, encoding=None):
    """提取Response的文本"""
    if encoding:
        resp.encoding = encoding
    else:
        resp.encoding = resp.apparent_encoding
    return resp.text


def get_html(url, encoding='utf-8'):
    """使用get方法访问指定网页并返回经lxml解析后的document"""
    resp = request_get(url)
    text = get_resp_text(resp, encoding=encoding)
    html = lxml.html.fromstring(text)
    html.make_links_absolute(url, resolve_base_href=True)
    # 清理功能仅应在需要的时候用来调试网页（如prestige），否则可能反过来影响调试（如JavBus）
    # html = cleaner.clean_html(html)
    if hasattr(sys, 'javsp_debug_mode'):
        lxml.html.open_in_browser(html, encoding=encoding)  # for develop and debug
    return html


def resp2html(resp, encoding='utf-8') -> lxml.html.HtmlComment:
    """将request返回的response转换为经lxml解析后的document"""
    text = get_resp_text(resp, encoding=encoding)
    html = lxml.html.fromstring(text)
    html.make_links_absolute(resp.url, resolve_base_href=True)
    # html = cleaner.clean_html(html)
    if hasattr(sys, 'javsp_debug_mode'):
        lxml.html.open_in_browser(html, encoding=encoding)  # for develop and debug
    return html


def post_html(url, data, encoding='utf-8', cookies={}):
    """使用post方法访问指定网页并返回经lxml解析后的document"""
    resp = request_post(url, data, cookies=cookies)
    text = get_resp_text(resp, encoding=encoding)
    html = lxml.html.fromstring(text)
    # jav321提供ed2k形式的资源链接，其中的非ASCII字符可能导致转换失败，因此要先进行处理
    ed2k_tags = html.xpath("//a[starts-with(@href,'ed2k://')]")
    for tag in ed2k_tags:
        tag.attrib['ed2k'], tag.attrib['href'] = tag.attrib['href'], ''
    html.make_links_absolute(url, resolve_base_href=True)
    for tag in ed2k_tags:
        tag.attrib['href'] = tag.attrib['ed2k']
        tag.attrib.pop('ed2k')
    # html = cleaner.clean_html(html)
    # lxml.html.open_in_browser(html, encoding=encoding)  # for develop and debug
    return html


def dump_xpath_node(node, filename=None):
    """将xpath节点dump到文件"""
    if not filename:
        filename = node.tag + '.html'
    with open(filename, 'wt', encoding='utf-8') as f:
        content = etree.tostring(node, pretty_print=True).decode('utf-8')
        f.write(content)


def is_connectable(url, timeout=3):
    """测试与指定url的连接"""
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        return True
    except requests.exceptions.RequestException as e:
        logger.debug(f"Not connectable: {url}\n" + repr(e))
        return False


def urlretrieve(url, filename=None, reporthook=None, headers=None):
    if "arzon" in url:
        headers["Referer"] = "https://www.arzon.jp/"
    """使用requests实现urlretrieve"""
    # https://blog.csdn.net/qq_38282706/article/details/80253447
    with contextlib.closing(requests.get(url, headers=headers,
                                         proxies=read_proxy(), stream=True)) as r:
        header = r.headers
        with open(filename, 'wb+') as fp:
            bs = 1024
            size = -1
            blocknum = 0
            if "content-length" in header:
                size = int(header["Content-Length"])    # 文件总大小（理论值）
            if reporthook:                              # 写入前运行一次回调函数
                reporthook(blocknum, bs, size)
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    fp.write(chunk)
                    fp.flush()
                    blocknum += 1
                    if reporthook:
                        reporthook(blocknum, bs, size)  # 每写入一次运行一次回调函数


def download(url, output_path, desc=None):
    """下载指定url的资源"""
    # 支持“下载”本地资源，以供fc2fan的本地镜像所使用
    if not url.startswith('http'):
        start_time = time.time()
        shutil.copyfile(url, output_path)
        filesize = os.path.getsize(url)
        elapsed = time.time() - start_time
        info = {'total': filesize, 'elapsed': elapsed, 'rate': filesize/elapsed}
        return info
    if not desc:
        desc = url.split('/')[-1]
    referrer = headers.copy()
    referrer['referer'] = url[:url.find('/', 8)+1]  # 提取base_url部分
    with DownloadProgressBar(unit='B', unit_scale=True,
                             miniters=1, desc=desc, leave=False) as t:
        urlretrieve(url, filename=output_path, reporthook=t.update_to, headers=referrer)
        info = {k: t.format_dict[k] for k in ('total', 'elapsed', 'rate')}
        return info


def open_in_chrome(url, new=0, autoraise=True):
    """使用指定的Chrome Profile打开url，便于调试"""
    import subprocess
    chrome = R'C:\Program Files\Google\Chrome\Application\chrome.exe'
    subprocess.run(f'"{chrome}" --profile-directory="Profile 2" {url}', shell=True)

import webbrowser
webbrowser.open = open_in_chrome


if __name__ == "__main__":
    import pretty_errors  # type: ignore
    pretty_errors.configure(display_link=True)
    download('https://www.javbus.com/pics/cover/6n54_b.jpg', 'cover.jpg')
