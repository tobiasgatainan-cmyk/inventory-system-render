"""
notify.py ── 使用 Resend 寄送通知信
設定步驟：
  1. 到 https://resend.com 免費註冊
  2. 取得 API Key，設為 Render 環境變數 RESEND_API_KEY
  3. 設定寄件人 domain（或使用 Resend 預設的 onboarding@resend.dev 測試）
  4. 設定 MAIL_FROM 環境變數（預設 onboarding@resend.dev）
"""
import os
import json
import urllib.request
import urllib.error

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
MAIL_FROM      = os.environ.get('MAIL_FROM', 'onboarding@resend.dev')
SITE_URL       = os.environ.get('SITE_URL', 'https://inventory-system-render.onrender.com')


def _send(to_list: list, subject: str, html: str):
    if not RESEND_API_KEY:
        raise EnvironmentError('缺少環境變數 RESEND_API_KEY')
    payload = json.dumps({
        'from':    MAIL_FROM,
        'to':      to_list,
        'subject': subject,
        'html':    html,
    }).encode()
    req = urllib.request.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f'Bearer {RESEND_API_KEY}',
            'Content-Type':  'application/json',
            'Accept':        'application/json',
            'User-Agent':    'MeishanFoundation-InventorySystem/1.0 (+https://inventory-system-render.onrender.com)',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        try:
            detail = json.loads(body)
            msg = detail.get('message') or detail.get('name') or body
        except (json.JSONDecodeError, AttributeError):
            msg = body or str(e)
        raise RuntimeError(f'Resend 錯誤 ({e.code})：{msg}') from e


def _get_notify_recipients():
    """取得所有開啟通知的使用者信箱"""
    from main import User
    recipients = []
    for u in User.query.filter_by(notify_on=True).all():
        if u.notify_email:
            recipients.append(u.notify_email)
    return recipients


def send_order_notify(order):
    """新申請單通知管理員"""
    recipients = _get_notify_recipients()
    if not recipients:
        return

    rows = ''.join(
        f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee">{oi.item_name}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{oi.brand_name}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{oi.spec_name}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center">{oi.qty_request}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{oi.expiry_str}</td></tr>'
        for oi in order.items
    )
    admin_url = f'{SITE_URL}/admin/orders/{order.id}'
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
      <div style="background:#fe7b81;color:white;padding:16px 24px;border-radius:8px 8px 0 0">
        <h2 style="margin:0">📦 新申請單 {order.order_no}</h2>
      </div>
      <div style="background:#fff;padding:24px;border:1px solid #edddd0;border-top:none;border-radius:0 0 8px 8px">
        <p><strong>申請人：</strong>{order.applicant}</p>
        <p><strong>申請時間：</strong>{order.created_at.strftime('%Y-%m-%d %H:%M')}</p>
        {'<p><strong>備註：</strong>' + order.note + '</p>' if order.note else ''}
        <table style="width:100%;border-collapse:collapse;margin:16px 0;font-size:13px">
          <thead>
            <tr style="background:#fdf6f0">
              <th style="padding:8px 10px;text-align:left">品項</th>
              <th style="padding:8px 10px;text-align:left">品牌</th>
              <th style="padding:8px 10px;text-align:left">規格</th>
              <th style="padding:8px 10px;text-align:center">數量</th>
              <th style="padding:8px 10px;text-align:left">到期日</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <a href="{admin_url}" style="display:inline-block;padding:10px 20px;background:#fe7b81;color:white;text-decoration:none;border-radius:6px;font-weight:bold">
          前往後台處理
        </a>
      </div>
    </div>
    """
    _send(recipients, f'【庫存申請】{order.applicant} 提交申請單 {order.order_no}', html)


def send_test_email(to_email: str, username: str):
    """寄送測試信"""
    html = f"""
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto">
      <div style="background:#fe7b81;color:white;padding:16px 24px;border-radius:8px 8px 0 0">
        <h2 style="margin:0">✅ 通知信設定成功</h2>
      </div>
      <div style="background:#fff;padding:24px;border:1px solid #edddd0;border-top:none;border-radius:0 0 8px 8px">
        <p>您好 <strong>{username}</strong>，</p>
        <p>這是一封測試信，確認您的通知信箱 <strong>{to_email}</strong> 設定成功。</p>
        <p>之後有新的申請單送出時，您將收到通知。</p>
        <hr style="border:none;border-top:1px solid #eee;margin:16px 0">
        <p style="color:#aaa;font-size:12px">美善基金會庫存管理系統</p>
      </div>
    </div>
    """
    _send([to_email], '【庫存系統】通知信箱設定測試', html)
