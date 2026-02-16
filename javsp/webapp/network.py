import asyncio
import requests
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException

from .auth import get_current_user, UserInfo
from javsp.web.base import read_proxy

router = APIRouter(prefix="/network", tags=["network"])

# 爬虫站点列表及其测试URL
# 注意：某些站点可能在某些地区需要代理才能访问
CRAWLER_SITES = {
    "javbus": "https://www.javbus.com",
    "javdb": "https://javdb.com",
    "javlib": "https://www.javlib.com",
    "avsox": "https://avsox.host",
    "fanza": "https://www.dmm.co.jp",
    "mgstage": "https://www.mgstage.com",
    "fc2": "https://adult.contents.fc2.com",
    "njav": "https://njav.tv",
    "gyutto": "https://gyutto.com",
    "prestige": "https://www.prestige-av.com"
}


def test_site_connectivity_sync(site_name: str, url: str, timeout: int = 10) -> Dict[str, Any]:
    """同步测试单个站点的连通性"""
    try:
        # 使用与爬虫相同的代理配置
        proxies = read_proxy()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        # 如果配置了代理，在错误信息中说明
        proxy_info = f" (via proxy: {proxies.get('http', 'none')})" if proxies else " (direct connection)"

        response = requests.get(url, timeout=timeout, allow_redirects=True, headers=headers, proxies=proxies)
        return {
            "success": response.status_code < 400,
            "status_code": response.status_code,
            "error": None,
            "proxy_used": bool(proxies)
        }
    except Exception as e:
        proxies = read_proxy()
        proxy_info = f" (via proxy: {proxies.get('http', 'none')})" if proxies else " (direct connection)"
        return {
            "success": False,
            "status_code": None,
            "error": f"{str(e)}{proxy_info}",
            "proxy_used": bool(proxies)
        }


@router.post("/connectivity-test")
async def test_connectivity(user: UserInfo = Depends(get_current_user)) -> Dict[str, Dict[str, Any]]:
    """
    测试所有爬虫站点的连通性
    """
    results = {}

    # 使用线程池并发执行同步HTTP请求
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=5) as executor:
        # 创建并发任务
        tasks = [
            loop.run_in_executor(executor, test_site_connectivity_sync, site_name, url)
            for site_name, url in CRAWLER_SITES.items()
        ]

        # 等待所有任务完成
        test_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        for (site_name, _), result in zip(CRAWLER_SITES.items(), test_results):
            if isinstance(result, Exception):
                results[site_name] = {
                    "success": False,
                    "status_code": None,
                    "error": str(result)
                }
            else:
                results[site_name] = result

    return results
