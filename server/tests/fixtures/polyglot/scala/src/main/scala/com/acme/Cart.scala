package com.acme

case class Cart(items: List[String]) {
  def count: Int = items.size
}

trait Priced {
  def price(): Int
}

object Cart {
  def empty(): Cart = Cart(Nil)
}
