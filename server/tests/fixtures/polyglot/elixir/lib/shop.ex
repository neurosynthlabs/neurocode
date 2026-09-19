defmodule Shop do
  alias Shop.Cart
  import Logger

  def checkout do
    Cart.add([], :apple)
  end
end
