require_relative "lib/cart"
require "json"

def checkout
  Shop::Cart.build.add("apple")
end
