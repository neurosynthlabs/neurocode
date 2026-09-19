defmodule Shop.Cart do
  def add(cart, item) do
    if item, do: [item | cart], else: cart
  end

  defp hidden, do: :ok
end
