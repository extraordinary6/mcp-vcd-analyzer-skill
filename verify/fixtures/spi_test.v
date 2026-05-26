`timescale 1ns/1ps

// SPI protocol test (Mode 0: CPOL=0, CPHA=0)
module spi_test;
    reg clk;
    reg rst_n;

    // SPI signals
    reg spi_sclk;
    reg spi_cs_n;  // Chip select (active low)
    reg spi_mosi;
    reg spi_miso;

    task send_byte_mode0(input [7:0] tx_data, input [7:0] rx_data);
        integer i;
        begin
            for (i = 7; i >= 0; i = i - 1) begin
                // Setup data on falling/rising edge (CPHA=0: data on first edge)
                spi_mosi = tx_data[i];
                spi_miso = rx_data[i];

                // Wait, then rising edge
                #50 spi_sclk = 1;
                // Slave samples on rising edge
                #50 spi_sclk = 0;
            end
        end
    endtask

    initial begin
        $dumpfile("spi_basic.vcd");
        $dumpvars(0, spi_test);

        clk = 0;
        rst_n = 0;
        spi_sclk = 0;   // CPOL=0
        spi_cs_n = 1;   // Inactive
        spi_mosi = 0;
        spi_miso = 0;

        #20 rst_n = 1;

        #50;

        // SPI Transaction 1: Send 0xA5, receive 0x5A
        spi_cs_n = 0;
        #50;
        send_byte_mode0(8'hA5, 8'h5A);
        #50 spi_cs_n = 1;

        #200;

        // SPI Transaction 2: Send 0xFF, receive 0x00
        spi_cs_n = 0;
        #50;
        send_byte_mode0(8'hFF, 8'h00);
        #50 spi_cs_n = 1;

        #100 $finish;
    end

    always #5 clk = ~clk;

endmodule
