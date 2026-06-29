# LLD: Parking Lot

## Problem

Design a parking lot system that can manage vehicle entry, exit, and fee collection across multiple floors and vehicle types.

## Scale hints

- Multi-story lot: up to 10 floors, 100 spots per floor
- Vehicle types: motorcycle, compact, large
- Multiple entry/exit gates
- Real-time spot availability display
- Fee calculation based on duration and vehicle type

## Topics to cover

- Class hierarchy: ParkingLot, Floor, Spot, Vehicle, Ticket
- Spot allocation strategy (nearest available)
- Concurrency: concurrent entry/exit requests
- Fee calculator as a strategy pattern
- Observer pattern for availability display
- Edge cases: full lot, invalid tickets, overnight stays
