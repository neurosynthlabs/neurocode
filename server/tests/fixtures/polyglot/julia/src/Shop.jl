module Shop

using LinearAlgebra
include("cart.jl")

total(c) = length(c.items)

end
