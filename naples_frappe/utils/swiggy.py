import frappe
import requests
import os
import sys
from frappe.utils import today
import questionary

session = requests.Session()
SWIGGY_LOGIN_URL = "https://partner.swiggy.com/authentication/v1/login/"
SWIGGY_ORDER_HISTORY_URL = "https://partner.swiggy.com/orders/v0/history/"


def login_to_swiggy():
  session.post(SWIGGY_LOGIN_URL, json={
      "username": "242760",
      "password": "Naples1717"
  })


def get_swiggy_orders(date):
  if frappe.cache().hget("swiggy_orders", date):
    print("Cache hit {}".format(date))
    return frappe.cache().hget("swiggy_orders", date)

  login_to_swiggy()
  r = session.get(SWIGGY_ORDER_HISTORY_URL, params={
      "restaurant_id": "242760",
      "ordered_time__gte": date,
      "ordered_time__lte": date,
      "offset": 0,
      "limit": 1000
  }).json()

  if r["statusCode"] != 0:
    print("Failed fetching orders", r)
  else:
    d = r["data"][0]["data"]["objects"]
    frappe.cache().hset("swiggy_orders", date, d)
    return d


def parse_swiggy_orders_as_invoices(orders, date):
  invs = []
  for order in orders:
    if order["status"]["order_status"] != "delivered":
      continue
    remarks = "Swiggy Order {}".format(order["order_id"])
    if len(frappe.get_all("Sales Invoice", filters={"remarks": remarks})) > 0:
      # already made
      continue

    pretty_print_swiggy_order(order)
    inv = frappe.get_doc(frappe._dict(
        doctype="Sales Invoice",
        debit_to="Debtors - NC",
        company="Naples Cafeteria",
        customer="Swiggy Order",
        set_posting_time=1,
        pos_profile="Swiggy",
        is_pos=1,
        posting_date=date,
        posting_time=order["status"]["ordered_time"].split()[1],
        remarks=remarks,
        payments=[{
            "amount": order["bill"],
            "mode_of_payment": "Swiggy",
            "account": "Swiggy - NC"
        }],
        conversion_factor=1))
    for item in order.get("cart").get("items"):
      item = frappe._dict(item)
      item_guess = guess_item_code(item.name)
      print(item_guess)
      inv.append("items", frappe._dict(item_code=item_guess, conversion_factor=1, uom="Nos",
                                       qty=item.quantity, rate=item.sub_total / item.quantity))

    parcel_box_qty = order.get("cart").get(
        "charges").get("packing_charge") / 5
    inv.append("items", frappe._dict(
        item_code="Parcel Box", conversion_factor=1, uom="Nos", qty=parcel_box_qty))
    inv._action = "save"
    inv.validate()
    invs.append(inv)
    print("\n\t" + "=" * 20)

  return invs


def guess_item_code(item):
  words = [x.lower() for x in item.split()]
  word_item_list = frappe._dict()
  word_item_count = frappe._dict()
  word_priority = []
  result_list = []
  for w in words:
    word_item_list[w] = frappe.db.sql("""
    SELECT
      item.name, price.price_list_rate
    FROM `tabItem` item
      JOIN `tabItem Price` price ON price.item_code = item.name
    WHERE
      item.name LIKE %(word)s
      AND price.selling = 1
      AND price.price_list = "Standard Selling";
    """, {"word": "%{}%".format(w)}, as_dict=1)

    word_item_count[w] = len(word_item_list[w])
    priority_idx = 0
    for x in word_priority:
      if word_item_count[x] <= word_item_count[w]:
        priority_idx += 1
      else:
        break
    word_priority.insert(priority_idx, w)

  for x in word_priority:
    for y in word_item_list[x]:
      k = "{}    {}".format(y.price_list_rate, y.name)
      if k not in result_list:
        result_list.append(k)

  suggestion_idx = 0
  while True:
    start = suggestion_idx * 7
    end = (suggestion_idx + 1) * 7
    if end > len(result_list):
      end = len(result_list)

    guess = questionary.select(
      "Please select a matching Item for '{}'".format(item),
      choices=result_list[start:end] + ["-- More --"]
    ).ask()
    if guess != "-- More --":
      break
    suggestion_idx += 1
    if end == len(result_list):
      suggestion_idx = 0

  return guess.split("    ")[1]


def confirm_and_submit_invoices(invoices):
  print("\n\nPlease confirm:")
  total = 0
  for inv in invoices:
    total += inv.grand_total
    print("DT:{} CST:{} MOP:{}\tGT:{}".format(
        inv.posting_date,
        inv.customer, inv.payments[0].mode_of_payment, inv.grand_total))

  print("Day Total: ", total)
  proceed = questionary.confirm("Proceed to Submission ?").ask()
  if proceed:
    for inv in invoices:
      inv.submit()
    
    frappe.db.commit()


def pretty_print_swiggy_order(order):
  print("")
  for item in order.get("cart").get("items"):
    item = frappe._dict(item)
    print("{}\t{}\t{}".format(item.sub_total /
                              item.quantity, item.quantity, item.name))

  print("")
  print(" # {}".format(order["order_id"]))
  print("ParcelBx: \t{}".format(order["cart"]["charges"]["packing_charge"]))
  print("Order Value: \t{}".format(order["bill"]))
  if order.get("discount"):
    print("Discount: \t{}\n".format(order["discount"]))


if __name__ == "__main__":
  from .debug import wait_for_attach
  # wait_for_attach()

  try:
    os.chdir("./sites")
    frappe.init(site="erp.naplesfoods.in")
    frappe.connect()

    if len(sys.argv) > 1:
      date = sys.argv[1]
    else:
      date = today()

    orders = get_swiggy_orders(date)
    invoices = parse_swiggy_orders_as_invoices(orders, date)
    confirm_and_submit_invoices(invoices)
    # print([x.as_dict() for x in invoices])
  except:
    print(frappe.get_traceback())
  finally:
    frappe.destroy()
