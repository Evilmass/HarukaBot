# 打印对齐的数据
items = [
    {
        "username": "abcde",
        "time": "12 小时 40 分钟",
    },
    {
        "username": "中文字符串",
        "time": "4 小时 7 分钟",
    },
    {
        "username": "zx",
        "time": "3 小时 1 分钟",
    },
]
for r in items:
    tplt = "{0:^10}\t{1:^10}\n"
    print(tplt.format(r["username"].ljust(10), r["time"], chr(12288)), end="")
