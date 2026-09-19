package com.acme.app

import com.acme.Cart
import scala.util.Try

object Main {
  def main(args: Array[String]): Unit = println(Cart.empty().count)
}
