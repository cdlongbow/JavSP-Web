"""从JavLibrary抓取数据"""
import logging
from urllib.parse import urlsplit


from javsp.web.base import Request, read_proxy, resp2html
from javsp.web.exceptions import *
from javsp.web.proxyfree import get_proxy_free_url
from javsp.config import Cfg, CrawlerID
from javsp.datatype import  MovieInfo
from javsp.cookie_manager import get_cookie_manager

logger = logging.getLogger(__name__)

# 初始化Request实例
request = Request(use_scraper=True)

# 检查CookieCloud是否有javlibrary.com的cookies，如果有则直接设置
cookie_manager = get_cookie_manager()
domain = 'javlibrary.com'
cookies = cookie_manager.get_cookies_for_domain(domain, prefer_cookiecloud=True)
if cookies:
    request.cookies = cookies
    logger.debug(f'从CookieCloud获取到javlibrary.com的cookies ({len(cookies)}个)')


def get_html_wrapper(url):
    """包装外发的request请求并负责转换为可xpath的html，同时处理Cookies无效等问题

    Returns:
        tuple: (resp, html) - 响应对象和解析后的HTML对象
    """
    global request
    resp = request.get(url, delay_raise=True)

    # 检查HTTP状态码
    if resp.status_code != 200:
        content = resp.text.lower()
        if ('just a moment' in content or
            'checking your browser' in content or
            'cf-browser-verification' in content or
            'cloudflare' in content):
            from javsp.web.exceptions import SiteBlocked
            raise SiteBlocked(f"JavLib触发Cloudflare验证 (状态码: {resp.status_code})，需要手动验证或等待: {url}")
        elif resp.status_code == 403:
            from javsp.web.exceptions import SiteBlocked
            raise SiteBlocked(f"JavLib拒绝访问 (403 Forbidden)，可能需要登录或更换IP: {url}")
        elif resp.status_code >= 500:
            from javsp.web.exceptions import WebsiteError
            raise WebsiteError(f"JavLib服务器错误 (状态码: {resp.status_code}): {url}")
        else:
            # 对于其他状态码，抛出通用异常
            from javsp.web.exceptions import WebsiteError
            raise WebsiteError(f"JavLib请求失败 (状态码: {resp.status_code}): {url}")

    # 检查是否被重定向到登录页面（JavLibrary的登录页面URL通常包含'login'或'adult'等关键词）
    if resp.history and any(keyword in resp.url.lower() for keyword in ['login', 'adult', 'auth']):
        logger.debug(f'检测到登录重定向，cookies可能已失效，尝试重新获取')

        # 使用Cookie管理器重新获取cookies
        cookie_manager = get_cookie_manager()
        domain = 'javlibrary.com'

        # 优先尝试CookieCloud，然后尝试浏览器cookies
        cookies = cookie_manager.get_cookies_for_domain(domain, prefer_cookiecloud=True)

        if cookies:
            # 更换Cookies时需要创建新的request实例，否则cloudscraper会保留它内部第一次发起网络访问时获得的Cookies
            request = Request(use_scraper=True)
            request.cookies = cookies
            logger.debug(f'重新获取到cookies ({len(cookies)}个)，重试请求')
            return get_html_wrapper(url)
        else:
            from javsp.web.exceptions import CredentialError
            raise CredentialError(f'JavLibrary需要登录凭据，但CookieCloud和浏览器中都没有找到有效的cookies: {url}')

    return resp, resp2html(resp)

logger = logging.getLogger(__name__)
permanent_url = 'https://www.javlibrary.com'
base_url = ''


def init_network_cfg():
    """设置合适的代理模式和base_url"""
    request.timeout = 5
    proxy_free_url = get_proxy_free_url('javlib')
    urls = [str(Cfg().network.proxy_free[CrawlerID.javlib]), permanent_url]
    if proxy_free_url and proxy_free_url not in urls:
        urls.insert(1, proxy_free_url)
    # 使用代理容易触发IUAM保护，先尝试不使用代理访问
    proxy_cfgs = [{}, read_proxy()] if Cfg().network.proxy_server else [{}]

    cloudflare_detected = False
    last_cloudflare_error = None

    for proxies in proxy_cfgs:
        request.proxies = proxies
        for url in urls:
            if proxies == {} and url == permanent_url:
                continue
            try:
                resp = request.get(url, delay_raise=True)
                if resp.status_code == 200:
                    # 检查响应内容是否包含Cloudflare验证页面
                    content = resp.text.lower()
                    if ('just a moment' in content or
                        'checking your browser' in content or
                        'cf-browser-verification' in content):
                        cloudflare_detected = True
                        last_cloudflare_error = f"检测到Cloudflare验证页面，代理: {proxies or '无'}"
                        logger.debug(f"Cloudflare验证检测到: {url}")
                        continue
                    request.timeout = Cfg().network.timeout.seconds
                    return url
                elif resp.status_code == 403:
                    # 检查是否为Cloudflare错误
                    content = resp.text.lower()
                    if 'just a moment' in content:
                        cloudflare_detected = True
                        last_cloudflare_error = f"Cloudflare IUAM保护 (403 Forbidden)，代理: {proxies or '无'}"
                        logger.debug(f"Cloudflare IUAM检测到: {url}")
                        continue
                    else:
                        logger.debug(f"403错误但非Cloudflare: {url}")
            except Exception as e:
                logger.debug(f"Fail to connect to '{url}': {e}")
                # 检查异常信息是否包含Cloudflare相关内容
                error_str = str(e).lower()
                if 'cloudflare' in error_str or 'cf-' in error_str:
                    cloudflare_detected = True
                    last_cloudflare_error = f"Cloudflare相关错误: {str(e)}，代理: {proxies or '无'}"
                    logger.debug(f"Cloudflare异常检测到: {url} - {e}")

    if cloudflare_detected and last_cloudflare_error:
        logger.warning(f'JavLib触发Cloudflare保护: {last_cloudflare_error}')
    else:
        logger.warning('无法绕开JavLib的反爬机制')

    request.timeout = Cfg().network.timeout.seconds
    return permanent_url


# TODO: 发现JavLibrary支持使用cid搜索，会直接跳转到对应的影片页面，也许可以利用这个功能来做cid到dvdid的转换
def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    global base_url
    if not base_url:
        base_url = init_network_cfg()
        logger.debug(f"JavLib网络配置: {base_url}, proxy={request.proxies}")
    url = new_url = f'{base_url}/cn/vl_searchbyid.php?keyword={movie.dvdid}'
    movie.url = url
    resp, html = get_html_wrapper(url)

    # 检查响应内容是否包含Cloudflare验证
    if resp.status_code == 403 or resp.status_code >= 500:
        content = resp.text.lower()
        if ('just a moment' in content or
            'checking your browser' in content or
            'cf-browser-verification' in content or
            'cloudflare' in content):
            from javsp.web.exceptions import SiteBlocked
            raise SiteBlocked(f"JavLib触发Cloudflare验证 (状态码: {resp.status_code})，需要手动验证或等待: {url}")
        elif resp.status_code == 403:
            from javsp.web.exceptions import SiteBlocked
            raise SiteBlocked(f"JavLib拒绝访问 (403 Forbidden)，可能需要登录或更换IP: {url}")
        elif resp.status_code >= 500:
            from javsp.web.exceptions import WebsiteError
            raise WebsiteError(f"JavLib服务器错误 (状态码: {resp.status_code}): {url}")
    if resp.history:
        if urlsplit(resp.url).netloc == urlsplit(base_url).netloc:
            # 出现301重定向通常且新老地址netloc相同时，说明搜索到了影片且只有一个结果
            new_url = resp.url
            movie.url = new_url  # 更新为实际的影片页面URL
        else:
            # 重定向到了不同的netloc时，新地址并不是影片地址。这种情况下新地址中丢失了path字段，
            # 为无效地址（应该是JavBus重定向配置有问题），需要使用新的base_url抓取数据
            base_url = 'https://' + urlsplit(resp.url).netloc
            logger.warning(f"请将配置文件中的JavLib免代理地址更新为: {base_url}")
            return parse_data(movie)
    else:   # 如果有多个搜索结果则不会自动跳转，此时需要程序介入选择搜索结果
        video_tags = html.xpath("//div[@class='video'][@id]/a")
        # 通常第一部影片就是我们要找的，但是以免万一还是遍历所有搜索结果
        pre_choose = []
        for tag in video_tags:
            tag_dvdid = tag.xpath("div[@class='id']/text()")[0]
            if tag_dvdid.upper() == movie.dvdid.upper():
                pre_choose.append(tag)
        pre_choose_urls = [i.get('href') for i in pre_choose]
        match_count = len(pre_choose)
        if match_count == 0:
            raise MovieNotFoundError(__name__, movie.dvdid)
        elif match_count == 1:
            new_url = pre_choose_urls[0]
        elif match_count == 2:
            no_blueray = []
            for tag in pre_choose:
                if 'ブルーレイディスク' not in tag.get('title'):    # Blu-ray Disc
                    no_blueray.append(tag)
            no_blueray_count = len(no_blueray)
            if no_blueray_count == 1:
                new_url = no_blueray[0].get('href')
                logger.debug(f"'{movie.dvdid}': 存在{match_count}个同番号搜索结果，已自动选择封面比例正确的一个: {new_url}")
            else:
                # 两个结果中没有谁是蓝光影片，说明影片番号重复了
                raise MovieDuplicateError(__name__, movie.dvdid, match_count, pre_choose_urls)
        else:
            # 存在不同影片但是番号相同的情况，如MIDV-010
            raise MovieDuplicateError(__name__, movie.dvdid, match_count, pre_choose_urls)
        # 重新抓取网页
        _, html = get_html_wrapper(new_url)
    container = html.xpath("/html/body/div/div[@id='rightcolumn']")[0]
    title_tag = container.xpath("div/h3/a/text()")
    title = title_tag[0]
    cover = container.xpath("//img[@id='video_jacket_img']/@src")[0]
    info = container.xpath("//div[@id='video_info']")[0]
    dvdid = info.xpath("div[@id='video_id']//td[@class='text']/text()")[0]
    publish_date = info.xpath("div[@id='video_date']//td[@class='text']/text()")[0]
    duration = info.xpath("div[@id='video_length']//span[@class='text']/text()")[0]
    director_tag = info.xpath("//span[@class='director']/a/text()")
    if director_tag:
        movie.director = director_tag[0]
    producer = info.xpath("//span[@class='maker']/a/text()")[0]
    publisher_tag = info.xpath("//span[@class='label']/a/text()")
    if publisher_tag:
        movie.publisher = publisher_tag[0]
    score_tag = info.xpath("//span[@class='score']/text()")
    if score_tag:
        movie.score = score_tag[0].strip('()')
    genre = info.xpath("//span[@class='genre']/a/text()")
    actress = info.xpath("//span[@class='star']/a/text()")

    movie.dvdid = dvdid
    movie.url = new_url.replace(base_url, permanent_url)
    movie.title = title.replace(dvdid, '').strip()
    if cover.startswith('//'):  # 补全URL中缺少的协议段
        cover = 'https:' + cover
    movie.cover = cover
    movie.publish_date = publish_date
    movie.duration = duration
    movie.producer = producer
    movie.genre = genre
    movie.actress = actress


if __name__ == "__main__":
    import pretty_errors  # type: ignore
    pretty_errors.configure(display_link=True)
    base_url = permanent_url
    movie = MovieInfo('IPX-177')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        print(e)
