from flask import Flask
import os

app = Flask(__name__)

@app.route('/')
def hello():
    return '''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>美善基金會庫存管理系統</title>
<style>
body { font-family: Arial; background: #fdf6f0; margin: 0; padding: 20px; }
.topnav { background: #fe7b81; color: white; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
.search { width: 100%; padding: 10px; margin-bottom: 20px; border: 1px solid #ddd; border-radius: 8px; }
.items { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.item { background: white; padding: 12px; border-radius: 8px; border: 1px solid #ddd; }
.item-name { font-weight: bold; margin: 8px 0; }
.spec { display: flex; justify-content: space-between; padding: 6px; background: #f7ecdf; margin: 3px 0; border-radius: 4px; font-size: 12px; }
</style>
</head>
<body>
<div class="topnav">📦 美善基金會庫存管理系統 - 雲端版</div>
<input type="text" class="search" id="search" placeholder="搜尋品項..." onkeyup="filter()">
<div class="items" id="items"></div>
<script>
const items = [
  {"id":"1","cat":"文具","name":"A4 影印紙","unit":"包","specs":[{"sid":"s1a","name":"白色 80g","qty":24,"safe":5}],"supplier":"ABC紙業"},
  {"id":"2","cat":"文具","name":"Kokuyo 膠帶","unit":"捲","specs":[{"sid":"s2a","name":"透明","qty":3,"safe":5}],"supplier":"OO批發"},
  {"id":"3","cat":"文具","name":"黑色原子筆","unit":"盒","specs":[{"sid":"s3a","name":"0.5mm","qty":0,"safe":3}],"supplier":"XYZ文具"},
  {"id":"4","cat":"清潔","name":"75% 酒精噴劑","unit":"瓶","specs":[{"sid":"s4a","name":"500ml","qty":8,"safe":4}],"supplier":"清潔用品廠"},
  {"id":"5","cat":"清潔","name":"濕紙巾","unit":"包","specs":[{"sid":"s5a","name":"一般型","qty":12,"safe":3}],"supplier":"日用品店"},
  {"id":"6","cat":"電腦設備","name":"無線滑鼠","unit":"個","specs":[{"sid":"s6a","name":"黑色","qty":2,"safe":1}],"supplier":"電子產品城"},
  {"id":"7","cat":"電腦設備","name":"USB-C 集線器","unit":"個","specs":[{"sid":"s7a","name":"4 port","qty":0,"safe":2}],"supplier":"電子商城"},
  {"id":"8","cat":"茶水間","name":"咖啡膠囊","unit":"顆","specs":[{"sid":"s8a","name":"深焙","qty":45,"safe":20}],"supplier":"咖啡商"},
  {"id":"9","cat":"茶水間","name":"紙杯","unit":"包","specs":[{"sid":"s9a","name":"標準","qty":2,"safe":5}],"supplier":"飲料用品"},
  {"id":"10","cat":"文具","name":"訂書針","unit":"盒","specs":[{"sid":"s10a","name":"10號","qty":7,"safe":2}],"supplier":"文具批發"}
];

function filter() {
  const q = document.getElementById('search').value.toLowerCase();
  const filtered = items.filter(i => i.name.toLowerCase().includes(q));
  document.getElementById('items').innerHTML = filtered.map(item => `
    <div class="item">
      <div style="font-size:11px;color:#999">🏷️ ${item.cat}</div>
      <div class="item-name">${item.name}</div>
      ${item.specs.map(s => `<div class="spec"><span>${s.name}</span><span>${s.qty}${item.unit}</span></div>`).join('')}
      <div style="font-size:10px;color:#999;margin-top:8px;padding-top:8px;border-top:1px solid #ddd">📦 ${item.supplier}</div>
    </div>
  `).join('');
}
filter();
</script>
</body>
</html>'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
