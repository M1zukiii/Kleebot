"""Conservative local limits for explicit output-exhaustion requests."""
import re
import unicodedata


NUMBER = r'(?:\d[\d,]*(?:\.\d+)?|[零一二两三四五六七八九十百千万亿]+)'


def amount(value):
    value = value.replace(',', '')
    try:
        return float(value)
    except ValueError:
        pass
    digits = dict(zip('零一二三四五六七八九', range(10)))
    digits['两'] = 2
    total, digit = 0, 0
    for char in value:
        if char in digits:
            digit = digits[char]
        else:
            total += (digit or 1) * {'十':10,'百':100,'千':1000,'万':10000,'亿':100000000}[char]
            digit = 0
    return total + digit


def excessive_request(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    # A song/video title or search phrase is not a request to print its contents.
    if re.match(r'^(?:\s|<@!?\d+>)*(?:请|帮我|给我|\s)*(?:播放|放歌|点播|搜索|搜一下)', text):
        return ''
    # Discussion, refusal and quoted examples are not demands to produce output.
    if re.search(r'^(?:\s|<@!?\d+>)*(?:为什么|如何防止|怎么防止|如何避免|有人让我|有人要求|(?:请)?不要(?!停止)|别(?!停)|do not(?! stop)|don.t(?! stop))', text):
        return ''
    imperative = r'(?:背诵|默写|列出|输出|打印|写出|生成|重复|数到|recite|print|output|generate|repeat|count to)'
    pi = re.search(r'(?:圆周率|π|\bpi\b)', text)
    if pi and re.search(imperative, text):
        counts = re.findall('('+NUMBER+r')\s*(?:位|个?数字|digits?\b|decimal places?\b)', text)
        english_small = re.search(r'(?:first|前)\s*(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|hundred)\s+digits?', text)
        if (not counts and not english_small) or any(amount(n) > 200 for n in counts):
            return '不接受无上限或超过200位的圆周率背诵，请改成具体的小范围问题。'
    if re.search(imperative, text):
        if re.search(r'(?:无限|永远|不要停止|直到我说停|耗尽.{0,8}(?:token|额度)|用完.{0,8}(?:token|额度)|forever|endless|infinite|until.{0,15}(?:limit|stop)|(?:一直|不停).{0,4}(?:输出|打印|重复|背诵|数数))', text):
            return '不接受无限重复、持续输出或故意耗尽额度的请求，请给出明确且合理的范围。'
        counts = re.findall('('+NUMBER+r')\s*(?:个?字(?:符)?|tokens?\b|characters?\b|行|lines?\b)', text)
        repeats = re.findall('('+NUMBER+r')\s*(?:次|遍|times?\b)', text)
        if any(amount(n) >= 10000 for n in counts) or any(amount(n) >= 100 for n in repeats):
            return '这项输出或重复次数过大，请缩小范围后再试。'
    return ''
