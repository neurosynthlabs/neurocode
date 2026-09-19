local M = {}

function M.add(cart, item)
  if item then
    table.insert(cart, item)
  end
end

local function hidden()
end

function count(cart)
  return #cart
end

return M
