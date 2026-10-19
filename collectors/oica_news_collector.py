import requests
from bs4 import BeautifulSoup
from datetime import datetime
import json
import os
import urllib3


# 关闭SSL警告
urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)



def get_oica_news():

    url = "https://oica.net/news/"

    print("正在采集:", url)


    session = requests.Session()

    # 关闭系统代理
    session.trust_env = False


    response = session.get(
        url,
        verify=False,
        timeout=30,
        headers={
            "User-Agent":
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64)"
        }
    )


    print("网页状态:", response.status_code)



    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )



    # 保存原始HTML

    os.makedirs(
        "data/raw",
        exist_ok=True
    )


    with open(
        "data/raw/oica_page.html",
        "w",
        encoding="utf-8"
    ) as f:

        f.write(response.text)



    print("HTML已保存")



    news_list = []



    # 找新闻模块

    posts = soup.find_all(
        "div",
        class_="postie-post"
    )



    print(
        "发现新闻模块:",
        len(posts)
    )



    for post in posts:


        title_tag = post.find("b")


        link_tag = post.find("a")



        if title_tag and link_tag:


            title = title_tag.text.strip()


            link = link_tag.get("href")



            if title and link:


                news_list.append({

                    "title": title,

                    "url": link,

                    "source": "OICA",

                    "collect_time":
                    datetime.now().isoformat()

                })



    # =========================
    # 新闻去重
    # =========================

    unique_news = []

    seen_urls = set()



    for item in news_list:


        if item["url"] not in seen_urls:


            unique_news.append(item)


            seen_urls.add(
                item["url"]
            )



    return unique_news





def save_json(data):


    os.makedirs(
        "data/raw",
        exist_ok=True
    )


    file_path = (
        "data/raw/oica_news.json"
    )



    with open(
        file_path,
        "w",
        encoding="utf-8"
    ) as f:


        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=4
        )



    print(
        "\n数据保存完成:",
        file_path
    )






if __name__ == "__main__":


    data = get_oica_news()


    save_json(data)



    print(
        "\n======采集结果======"
    )


    print(
        "新闻数量:",
        len(data)
    )



    for item in data[:10]:


        print(
            "\n标题:",
            item["title"]
        )


        print(
            "链接:",
            item["url"]
        )