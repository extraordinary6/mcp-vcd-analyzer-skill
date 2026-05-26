`timescale 1ns/1ps

// UART protocol test (115200 baud, 1 start, 8 data, 1 stop, no parity)
// Bit time at 115200 baud = ~8.68us, but we use simplified timing for testing
module uart_test;
    reg clk;
    reg rst_n;
    reg uart_tx;
    reg uart_rx;

    // Simplified UART: 1 bit = 100ns
    parameter BIT_TIME = 100;

    task send_byte(input [7:0] data);
        integer i;
        begin
            // Start bit (0)
            uart_tx = 0;
            #BIT_TIME;

            // Data bits (LSB first)
            for (i = 0; i < 8; i = i + 1) begin
                uart_tx = data[i];
                #BIT_TIME;
            end

            // Stop bit (1)
            uart_tx = 1;
            #BIT_TIME;
        end
    endtask

    initial begin
        $dumpfile("uart_basic.vcd");
        $dumpvars(0, uart_test);

        clk = 0;
        rst_n = 0;
        uart_tx = 1;  // Idle is high
        uart_rx = 1;

        #20 rst_n = 1;

        // Idle for a bit
        #100;

        // Send 'A' (0x41) on TX
        send_byte(8'h41);

        // Brief gap
        #100;

        // Send 'B' (0x42) on TX
        send_byte(8'h42);

        // Brief gap
        #200;

        // Send 'C' (0x43)
        send_byte(8'h43);

        #100 $finish;
    end

    always #5 clk = ~clk;

endmodule
